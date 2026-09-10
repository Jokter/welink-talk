from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List


class McpError(RuntimeError):
    pass


def load_server(config_path: Path, server_name: str) -> Dict[str, Any]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpError(f"无法读取 MCP 配置：{config_path}") from exc
    servers = payload.get("mcpServers", {})
    server = servers.get(server_name) if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise McpError(f"MCP 配置中不存在服务器：{server_name}")
    if not server.get("command"):
        if server.get("url"):
            raise McpError("当前版本仅支持 stdio MCP；该服务器配置为 HTTP URL")
        raise McpError("MCP 服务器缺少 command")
    return server


def expand_environment(value: str) -> str:
    expanded = os.path.expandvars(value)
    for name, item in os.environ.items():
        expanded = expanded.replace(f"${{{name}}}", item)
    return expanded


def build_process(server: Dict[str, Any]) -> tuple[List[str], Dict[str, str], str | None]:
    command = str(server["command"])
    args = [str(item) for item in server.get("args", [])]
    resolved = shutil.which(command) or command
    process_command = [resolved, *args]
    if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline(process_command)
        process_command = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", command_line]

    environment = os.environ.copy()
    raw_environment = server.get("env", {})
    if isinstance(raw_environment, dict):
        for name, value in raw_environment.items():
            environment[str(name)] = expand_environment(str(value))
    cwd = str(server["cwd"]) if server.get("cwd") else None
    return process_command, environment, cwd


class StdioMcpClient:
    def __init__(self, server: Dict[str, Any], timeout: float = 60):
        command, environment, cwd = build_process(server)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=cwd,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except OSError as exc:
            raise McpError("无法启动 MCP 服务器，请检查 command 和 args") from exc
        self.timeout = timeout
        self.next_id = 1
        self.responses: queue.Queue[Dict[str, Any]] = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def _read_stdout(self) -> None:
        if self.process.stdout is None:
            return
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self.responses.put(message)

    def _write(self, message: Dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise McpError("MCP 服务器已退出")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        deferred: List[Dict[str, Any]] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"MCP 请求超时：{method}")
                try:
                    response = self.responses.get(timeout=remaining)
                except queue.Empty as exc:
                    raise McpError(f"MCP 请求超时：{method}") from exc
                if response.get("id") != request_id:
                    deferred.append(response)
                    continue
                if "error" in response:
                    error = response.get("error", {})
                    message = error.get("message", "未知错误") if isinstance(error, dict) else "未知错误"
                    raise McpError(f"MCP 调用失败：{message}")
                result = response.get("result", {})
                return result if isinstance(result, dict) else {}
        finally:
            for response in deferred:
                self.responses.put(response)

    def notify(self, method: str, params: Dict[str, Any] | None = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def initialize(self) -> None:
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "welink-talk", "version": "1.0.0"},
        })
        self.notify("notifications/initialized")

    def list_tools(self) -> List[str]:
        result = self.request("tools/list", {})
        tools = result.get("tools", [])
        return [str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError(f"MCP 工具返回失败：{name}")
        return result

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()


def with_client(config_path: Path, server_name: str, timeout: float = 60) -> StdioMcpClient:
    client = StdioMcpClient(load_server(config_path, server_name), timeout=timeout)
    try:
        client.initialize()
    except Exception:
        client.close()
        raise
    return client


def list_mcp_tools(config_path: Path, server_name: str, timeout: float = 60) -> List[str]:
    client = with_client(config_path, server_name, timeout)
    try:
        return client.list_tools()
    finally:
        client.close()


def call_mcp_tool(
    config_path: Path,
    server_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
    timeout: float = 60,
) -> Dict[str, Any]:
    client = with_client(config_path, server_name, timeout)
    try:
        tools = client.list_tools()
        if tool_name not in tools:
            raise McpError(f"MCP 服务器不提供工具：{tool_name}")
        return client.call_tool(tool_name, arguments)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal stdio MCP client")
    parser.add_argument("--config", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args()
    try:
        if not args.list_tools:
            parser.error("目前仅支持 --list-tools 命令行检查")
        print(json.dumps({"tools": list_mcp_tools(Path(args.config), args.server, args.timeout)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
