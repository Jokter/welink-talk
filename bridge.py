from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def save_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temp.replace(path)


def hidden_process_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def run_process(
    command: List[str],
    timeout: int,
    cwd: Optional[str] = None,
    stdin_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=stdin_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        cwd=cwd or None,
        creationflags=hidden_process_flags(),
        check=False,
    )


def first_value(value: Any, keys: Iterable[str]) -> Any:
    if not isinstance(value, dict):
        return None
    lowered = {str(key).lower(): item for key, item in value.items()}
    for key in keys:
        found = lowered.get(key.lower())
        if found not in (None, ""):
            return found
    return None


def walk_objects(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_objects(nested)


def normalize_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                nested = json.loads(text)
                inner = first_value(nested, ["text", "content", "message", "body"])
                if isinstance(inner, str):
                    return inner.strip()
            except json.JSONDecodeError:
                pass
        return text
    if isinstance(value, dict):
        inner = first_value(value, ["text", "content", "message", "body"])
        return normalize_text(inner)
    return ""


def parse_messages(raw: str, conversation_key: str) -> List[Dict[str, str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WeLink CLI 未返回 JSON，请确认当前版本支持结构化历史消息输出") from exc

    messages: List[Dict[str, str]] = []
    id_keys = ["messageId", "msgId", "clientMsgId", "id", "message_id", "msg_id"]
    sender_keys = [
        "senderAccount", "fromAccount", "userAccount", "w3account",
        "sender", "from", "senderId", "fromUserId", "sourceAccount",
    ]
    text_keys = ["text", "content", "messageContent", "msgContent", "body"]
    time_keys = ["sendTime", "createTime", "timestamp", "time", "serverTime"]

    for obj in walk_objects(payload):
        text = normalize_text(first_value(obj, text_keys))
        if not text:
            continue
        sender_value = first_value(obj, sender_keys)
        if isinstance(sender_value, dict):
            sender_value = first_value(sender_value, ["w3account", "account", "id", "userId"])
        sender = str(sender_value or "").strip()
        timestamp = str(first_value(obj, time_keys) or "")
        message_id = str(first_value(obj, id_keys) or "").strip()
        if not sender and not message_id:
            continue
        if not message_id:
            seed = "\x1f".join([conversation_key, sender, timestamp, text])
            message_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        messages.append({
            "id": message_id,
            "sender": sender,
            "text": text,
            "timestamp": timestamp,
        })

    unique = {message["id"]: message for message in messages}

    def message_order(item: Dict[str, str]) -> Tuple[int, Any, str]:
        try:
            return 0, float(item["timestamp"]), item["id"]
        except ValueError:
            return 1, item["timestamp"], item["id"]

    return sorted(unique.values(), key=message_order)


def clean_final_text(text: str) -> str:
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.I | re.S)
    text = re.sub(r"```(?:analysis|reasoning)\s*.*?```", "", text, flags=re.I | re.S)
    return text.strip()


def final_from_json(value: Any) -> str:
    if isinstance(value, str):
        return clean_final_text(value)
    if isinstance(value, list):
        answers = [final_from_json(item) for item in value]
        return "\n".join(item for item in answers if item).strip()
    if not isinstance(value, dict):
        return ""

    for key in ["final", "finalAnswer", "final_answer", "answer", "result", "output_text"]:
        candidate = first_value(value, [key])
        answer = final_from_json(candidate)
        if answer:
            return answer

    event_type = str(first_value(value, ["type", "event", "role", "status"]) or "").lower()
    if any(marker in event_type for marker in ["final", "result", "completed", "assistant"]):
        candidate = first_value(value, ["content", "text", "output", "message"])
        return final_from_json(candidate)
    return ""


def extract_final_answer(stdout: str) -> str:
    stdout = stdout.strip()
    if not stdout:
        return ""
    try:
        answer = final_from_json(json.loads(stdout))
        if answer:
            return answer
    except json.JSONDecodeError:
        pass

    final_events: List[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        answer = final_from_json(event)
        if answer:
            final_events.append(answer)
    if final_events:
        return clean_final_text(final_events[-1])
    return clean_final_text(stdout)


class Bridge:
    def __init__(self, config_path: Path, dry_run: bool = False):
        self.config = load_json(config_path, None)
        if not isinstance(self.config, dict):
            raise RuntimeError(f"配置文件不存在或格式错误：{config_path}")
        if not isinstance(self.config.get("pi"), dict):
            raise RuntimeError("配置尚未切换到 Pi，请重新执行 setup.ps1")
        self.state_path = ROOT / self.config.get("state_file", "state.json")
        self.state = load_json(self.state_path, {"seen": [], "chats": {}, "bootstrapped": False})
        self.seen_order = list(self.state.get("seen", []))
        self.seen = set(self.seen_order)
        self.dry_run = dry_run
        self.welink_cli = self.config.get("welink_cli", "welink-cli")
        self.auth_ready = not self.config.get("auto_refresh_auth", True)
        self.next_auth_refresh_at = 0.0
        self.last_auth_error = ""

    def refresh_auth(self, force: bool = False) -> None:
        if not self.config.get("auto_refresh_auth", True):
            return
        now = time.monotonic()
        if not force and now < self.next_auth_refresh_at:
            if not self.auth_ready:
                raise RuntimeError(self.last_auth_error or "WeLink authentication is unavailable")
            return

        environment = str(self.config.get("welink_env", "pro"))
        timeout = int(self.config.get("auth_refresh_timeout_seconds", 60))
        result = run_process(
            [self.welink_cli, "auth", "login", "--env", environment],
            timeout=timeout,
        )
        if result.returncode != 0:
            self.last_auth_error = (
                "WeLink token refresh failed. Ensure WeLink PC is signed in, then run "
                f"`welink-cli auth login --env {environment}`."
            )
            self.next_auth_refresh_at = now + float(
                self.config.get("auth_refresh_retry_seconds", 60)
            )
            if not self.auth_ready:
                raise RuntimeError(self.last_auth_error)
            return

        self.auth_ready = True
        self.last_auth_error = ""
        self.next_auth_refresh_at = now + float(
            self.config.get("auth_refresh_interval_seconds", 1200)
        )
        print("WeLink token refreshed.", flush=True)

    def run_welink(self, command: List[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
        self.refresh_auth()
        result = run_process(command, timeout=timeout)
        if result.returncode == 0 or not self.config.get("auto_refresh_auth", True):
            return result

        self.refresh_auth(force=True)
        return run_process(command, timeout=timeout)

    def save_state(self) -> None:
        self.seen_order = self.seen_order[-5000:]
        self.seen = set(self.seen_order)
        self.state["seen"] = self.seen_order
        save_json(self.state_path, self.state)

    def mark_seen(self, message_id: str) -> None:
        if message_id not in self.seen:
            self.seen.add(message_id)
            self.seen_order.append(message_id)

    def conversations(self) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
        for chat in self.config.get("private_chats", []):
            yield f"user:{chat['account']}", "user", chat
        for chat in self.config.get("group_chats", []):
            yield f"group:{chat['group_id']}", "group", chat

    def query(self, kind: str, chat: Dict[str, Any]) -> List[Dict[str, str]]:
        command = [self.welink_cli, "im", "query-history-message"]
        if kind == "user":
            command += ["--user-account", chat["account"]]
        else:
            command += ["--group-id", chat["group_id"]]
        command += ["--query-count", "1"]
        result = self.run_welink(command, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "查询 WeLink 消息失败")
        key = f"{kind}:{chat.get('account') or chat.get('group_id')}"
        return parse_messages(result.stdout, key)

    def send(self, kind: str, chat: Dict[str, Any], text: str) -> None:
        max_chars = int(self.config.get("reply_max_chars", 3500))
        parts = [text[index:index + max_chars] for index in range(0, len(text), max_chars)] or [""]
        for index, part in enumerate(parts, start=1):
            prefix = f"[{index}/{len(parts)}]\n" if len(parts) > 1 else ""
            if self.dry_run:
                print(f"DRY-RUN reply to {kind}: {prefix}{part}")
                continue
            if kind == "user":
                command = [self.welink_cli, "im", "send-to-user", "--receiver", chat["account"]]
            else:
                command = [self.welink_cli, "im", "send-to-group", "--group-id", chat["group_id"]]
            result = self.run_welink(command + ["--text", prefix + part], timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "发送 WeLink 消息失败")

    def chat_state(self, key: str) -> Dict[str, Any]:
        chats = self.state.setdefault("chats", {})
        pi = self.config["pi"]
        default_model = pi["default_model"]
        default_directory = pi.get("working_directory") or str(ROOT)
        if key not in chats:
            chats[key] = {
                "model": default_model,
                "history": [],
                "working_directory": default_directory,
            }
        chat = chats[key]
        if chat.get("model") not in pi["models"]:
            chat["model"] = default_model
            chat["history"] = []
        try:
            chat["working_directory"] = str(self.resolve_directory(
                chat.get("working_directory") or default_directory
            ))
        except (OSError, ValueError):
            chat["working_directory"] = str(self.resolve_directory(default_directory))
        return chat

    def allowed_roots(self) -> List[Path]:
        pi = self.config["pi"]
        values = pi.get("allowed_working_roots") or [pi.get("working_directory") or str(ROOT)]
        return [Path(str(value)).expanduser().resolve() for value in values]

    def resolve_directory(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"目录不存在：{value}")
        normalized = os.path.normcase(os.path.abspath(str(path)))
        for root in self.allowed_roots():
            normalized_root = os.path.normcase(os.path.abspath(str(root)))
            try:
                if os.path.commonpath([normalized, normalized_root]) == normalized_root:
                    return path
            except ValueError:
                continue
        raise ValueError("目录不在允许的工作区内")

    @staticmethod
    def child_directories(current: Path) -> List[Path]:
        try:
            return sorted(
                (item for item in current.iterdir() if item.is_dir() and not item.name.startswith(".")),
                key=lambda item: item.name.lower(),
            )
        except OSError as exc:
            raise ValueError(f"无法读取目录：{exc}") from exc

    def directory_reply(self, key: str, argument: str) -> str:
        chat = self.chat_state(key)
        current = self.resolve_directory(chat["working_directory"])
        action, _, value = argument.strip().partition(" ")
        action = action or "help"

        if action in {"帮助", "help"}:
            return self.help_reply("dir")

        if action in {"当前", "current"}:
            return f"当前目录：{current}"
        if action in {"默认", "default", "root"}:
            target = self.resolve_directory(str(self.config["pi"].get("working_directory") or self.allowed_roots()[0]))
        elif action in {"返回", "上级", "back", "up"}:
            target = self.resolve_directory(str(current.parent))
        elif action in {"列表", "list", "ls"}:
            children = self.child_directories(current)
            if not children:
                return f"当前目录没有子目录：{current}"
            rows = [f"当前目录：{current}"]
            rows.extend(f"{index}. {item.name}" for index, item in enumerate(children[:50], start=1))
            if len(children) > 50:
                rows.append(f"还有 {len(children) - 50} 个目录未显示")
            rows.append("发送 /dir cd <序号或项目名>")
            return "\n".join(rows)
        elif action in {"进入", "切换", "enter", "cd"}:
            if not value:
                return "请指定项目路径、项目名或 /dir list 中的序号。"
            children = self.child_directories(current)
            if value.isdigit() and 1 <= int(value) <= len(children):
                candidate = children[int(value) - 1]
            else:
                requested = Path(value).expanduser()
                candidate = requested if requested.is_absolute() else current / requested
            target = self.resolve_directory(str(candidate))
        elif action in {"工作区", "roots"}:
            rows = ["允许的工作区："]
            rows.extend(f"{index}. {root}" for index, root in enumerate(self.allowed_roots(), start=1))
            return "\n".join(rows)
        else:
            return f"不支持的 dir 子命令：{action}\n\n{self.help_reply('dir')}"

        chat["working_directory"] = str(target)
        chat["history"] = []
        self.save_state()
        return f"已切换目录：{target}\n已开启新对话。"

    def model_reply(self, key: str, argument: str) -> str:
        pi = self.config["pi"]
        models = pi["models"]
        chat = self.chat_state(key)
        selected = argument.strip()
        if not selected or selected in {"帮助", "help"}:
            return self.help_reply("模型")
        if selected == "列表":
            rows = ["可用模型："]
            for index, model in enumerate(models, start=1):
                mark = "（当前）" if model == chat["model"] else ""
                rows.append(f"{index}. {model}{mark}")
            rows.append("发送 /模型 切换 <序号或模型名> 进行切换")
            return "\n".join(rows)

        if selected == "当前":
            return f"当前模型：{chat['model']}"
        if selected.startswith("切换 "):
            selected = selected[3:].strip()
        else:
            return f"不支持的模型子命令：{selected}\n\n{self.help_reply('模型')}"
        if selected.isdigit() and 1 <= int(selected) <= len(models):
            selected = models[int(selected) - 1]
        exact = next((model for model in models if model.lower() == selected.lower()), None)
        if not exact:
            return f"模型不存在：{selected}\n\n{self.help_reply('模型')}"
        chat["model"] = exact
        chat["history"] = []
        self.save_state()
        return f"已切换到 {exact}，并开启新会话。"

    @staticmethod
    def help_reply(topic: str = "") -> str:
        normalized = topic.strip().lower()
        if normalized in {"模型", "model"}:
            return (
                "模型命令：\n"
                "/模型 列表    查看可用模型\n"
                "/模型 当前    查看当前模型\n"
                "/模型 切换 2    按序号切换\n"
                "/模型 切换 <模型名>    按名称切换\n"
                "/模型 help    显示本帮助"
            )
        if normalized == "dir":
            return (
                "目录命令：\n"
                "/dir current    查看当前项目目录\n"
                "/dir roots    查看允许的工作区\n"
                "/dir list    查看当前目录的子项目\n"
                "/dir cd 2    按序号进入项目\n"
                "/dir cd <路径或名称>    切换项目\n"
                "/dir back    返回上一级\n"
                "/dir root    返回默认目录\n"
                "/dir help    显示本帮助"
            )
        if normalized in {"新对话", "new"}:
            return "新对话命令：\n/新对话    清除当前项目的最近对话上下文\n/新对话 help    显示本帮助"
        return (
            "可用命令：\n"
            "/帮助 模型    查看模型命令\n"
            "/帮助 dir    查看项目目录命令\n"
            "/帮助 新对话    查看新对话命令\n"
            "/模型 help    查看模型子命令\n"
            "/dir help    查看目录子命令\n"
            "/新对话    清除当前对话\n"
            "/问题内容    直接询问 Pi"
        )

    def make_prompt(self, chat: Dict[str, Any], user_text: str) -> str:
        history = chat.get("history", [])
        limit = int(self.config["pi"].get("history_turns", 6)) * 2
        lines = [
            "你正在通过手机聊天窗口回答用户。只输出最终答案，不展示思考过程、分析过程、工具调用过程或内部日志。",
            "回答应适合手机阅读。",
        ]
        if history:
            lines.append("\n以下是最近对话：")
            for item in history[-limit:]:
                role = "用户" if item["role"] == "user" else "助手"
                lines.append(f"{role}：{item['content']}")
        lines.append(f"\n用户的新消息：{user_text}")
        return "\n".join(lines)

    def ask_pi(self, key: str, user_text: str) -> str:
        pi = self.config["pi"]
        chat = self.chat_state(key)
        model = chat["model"]
        prompt = self.make_prompt(chat, user_text)
        replacements = {"model": model, "prompt": prompt}
        command = [str(part).format(**replacements) for part in pi["command"]]
        stdin_text = prompt if pi.get("prompt_via_stdin", True) else None
        result = run_process(
            command,
            timeout=int(pi.get("timeout_seconds", 900)),
            cwd=chat.get("working_directory") or pi.get("working_directory") or None,
            stdin_text=stdin_text,
        )
        if result.returncode != 0:
            detail = result.stderr.strip()
            raise RuntimeError(f"Pi 执行失败：{detail or '请在电脑端检查 Pi 登录和配置'}")
        answer = extract_final_answer(result.stdout)
        if not answer:
            raise RuntimeError("Pi 没有返回可识别的最终答案")
        history = chat.setdefault("history", [])
        history.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer},
        ])
        chat["history"] = history[-int(pi.get("history_turns", 6)) * 2:]
        self.save_state()
        return answer

    def handle(self, key: str, kind: str, chat: Dict[str, Any], text: str) -> None:
        text = text.strip()
        prefix = str(chat.get("trigger_prefix", "/"))
        if not prefix or not text.startswith(prefix):
            return
        text = text[len(prefix):].strip()
        if not text:
            return
        print(f"Received command from {key}.", flush=True)
        command, _, argument = text.partition(" ")
        command = command.lower()
        if command in {"帮助", "help"}:
            answer = self.help_reply(argument)
        elif command in {"模型", "model"}:
            answer = self.model_reply(key, argument)
        elif command == "dir":
            answer = self.directory_reply(key, argument)
        elif command in {"新对话", "new"}:
            if argument.strip() in {"帮助", "help"}:
                answer = self.help_reply("新对话")
            else:
                self.chat_state(key)["history"] = []
                self.save_state()
                answer = "已开启新会话。"
        else:
            answer = self.ask_pi(key, text)
        self.send(kind, chat, answer)

    def poll_once(self) -> None:
        bootstrap = not self.state.get("bootstrapped", False)
        for key, kind, chat in self.conversations():
            allowed = {str(item).lower() for item in chat.get("allowed_senders", [])}
            messages = self.query(kind, chat)
            for message in messages:
                if message["id"] in self.seen:
                    continue
                self.mark_seen(message["id"])
                if bootstrap and self.config.get("ignore_existing_on_first_start", True):
                    continue
                if allowed and message["sender"].lower() not in allowed:
                    continue
                try:
                    self.handle(key, kind, chat, message["text"])
                except subprocess.TimeoutExpired:
                    self.send(kind, chat, "本次处理超时，请缩小问题后重试。")
                except Exception as exc:
                    self.log_error(key, exc)
                    self.send(kind, chat, f"处理失败：{exc}")
        self.state["bootstrapped"] = True
        self.save_state()

    @staticmethod
    def log_error(key: str, exc: Exception) -> None:
        message = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{key}] {type(exc).__name__}: {exc}"
        with (ROOT / "bridge.log").open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")
        print(message, file=sys.stderr, flush=True)

    def run(self, once: bool = False) -> None:
        print(
            f"WeLink Pi bridge started. Polling every "
            f"{self.config.get('poll_interval_seconds', 5)} seconds.",
            flush=True,
        )
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                return
            except Exception as exc:
                self.log_error("poll", exc)
            if once:
                return
            time.sleep(float(self.config.get("poll_interval_seconds", 5)))


def main() -> int:
    parser = argparse.ArgumentParser(description="WeLink ↔ Pi bridge")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        Bridge(Path(args.config), dry_run=args.dry_run).run(once=args.once)
        return 0
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
