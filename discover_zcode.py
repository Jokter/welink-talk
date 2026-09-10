from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple


MODEL_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{1,99}$")
SKIP_DIRECTORIES = {"cache", "logs", "log", "node_modules", "tmp", "temp"}


def unique(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        cleaned = value.strip().strip("\"'")
        if not MODEL_VALUE.fullmatch(cleaned):
            continue
        lowered = cleaned.lower()
        if lowered in seen or lowered in {
            "argument", "arguments", "command", "commands", "current", "default",
            "description", "example", "examples", "help", "model", "models", "name",
            "model:", "models:", "option", "options", "usage", "usage:", "version",
        }:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result


def executable_score(path: Path) -> Tuple[int, int, str]:
    suffix_score = {".exe": 4, ".cmd": 3, ".bat": 2, "": 1}.get(path.suffix.lower(), 0)
    bin_score = 2 if path.parent.name.lower() == "bin" else 0
    return suffix_score + bin_score, -len(path.parts), str(path).lower()


def find_command(root: Path) -> List[str]:
    from_path = shutil.which("zcode")
    if from_path:
        return [str(Path(from_path).resolve())]

    candidates: List[Path] = []
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part.lower() in SKIP_DIRECTORIES for part in path.parts):
                continue
            if path.name.lower() in {"zcode.exe", "zcode.cmd", "zcode.bat", "zcode"}:
                candidates.append(path)
    if candidates:
        return [str(max(candidates, key=executable_score).resolve())]

    node = shutil.which("node")
    if node and root.is_dir():
        for package_path in root.rglob("package.json"):
            if any(part.lower() in SKIP_DIRECTORIES for part in package_path.parts):
                continue
            try:
                package = json.loads(package_path.read_text(encoding="utf-8"))
                binary = package.get("bin")
                relative = binary.get("zcode") if isinstance(binary, dict) else binary
                if isinstance(relative, str):
                    entry = (package_path.parent / relative).resolve()
                    if entry.is_file():
                        return [str(Path(node).resolve()), str(entry)]
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
    return []


def run(command: Sequence[str], timeout: int = 8) -> Tuple[int, str]:
    try:
        result = subprocess.run(
            list(command),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        return result.returncode, (result.stdout + "\n" + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def discover_arguments(help_text: str) -> Tuple[List[str], bool, List[str]]:
    lowered = help_text.lower()
    arguments: List[str] = []
    warnings: List[str] = []

    if re.search(r"(?<![\w-])--model(?:[\s=,]|$)", lowered):
        arguments += ["--model", "{model}"]
    elif re.search(r"(?<!\w)-m(?:[\s,]|$)", lowered):
        arguments += ["-m", "{model}"]
    else:
        warnings.append("The model option was not identified from zcode --help.")

    if "--output-format" in lowered and "json" in lowered:
        arguments += ["--output-format", "json"]
    elif re.search(r"(?<![\w-])--json(?:[\s,]|$)", lowered):
        arguments.append("--json")
    elif "--format" in lowered and "json" in lowered:
        arguments += ["--format", "json"]

    for flag in ("--final-only", "--print"):
        if flag in lowered:
            arguments.append(flag)
            break

    if re.search(r"(?<![\w-])--prompt(?:[\s=,]|$)", lowered):
        arguments += ["--prompt", "{prompt}"]
        prompt_via_stdin = False
    elif re.search(r"(?<![\w-])--message(?:[\s=,]|$)", lowered):
        arguments += ["--message", "{prompt}"]
        prompt_via_stdin = False
    elif "stdin" in lowered or "standard input" in lowered or "pipe" in lowered:
        prompt_via_stdin = True
    elif re.search(r"(?:usage:|arguments?:).*\bprompt\b", lowered, re.S):
        arguments.append("{prompt}")
        prompt_via_stdin = False
    else:
        prompt_via_stdin = True
        warnings.append("Prompt input mode was not identified; stdin was selected.")

    return arguments, prompt_via_stdin, warnings


def model_values(value: Any, parent_key: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower().replace("-", "_")
            if "model" in lowered:
                if isinstance(nested, str):
                    yield nested
                elif isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, str):
                            yield item
                        elif isinstance(item, dict):
                            for name_key in ("id", "name", "model", "model_id"):
                                if isinstance(item.get(name_key), str):
                                    yield item[name_key]
            yield from model_values(nested, lowered)
    elif isinstance(value, list):
        for nested in value:
            yield from model_values(nested, parent_key)


def default_model_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower().replace("-", "_")
            if "model" in lowered and "default" in lowered and isinstance(nested, str):
                yield nested
            yield from default_model_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from default_model_values(nested)


def listed_model_values(value: Any, parent_key: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower().replace("-", "_")
            if lowered in {"id", "name", "model", "model_id", "model_name"} and isinstance(nested, str):
                yield nested
            yield from listed_model_values(nested, lowered)
    elif isinstance(value, list):
        for nested in value:
            if isinstance(nested, str) and parent_key in {"", "data", "items", "models", "result"}:
                yield nested
            else:
                yield from listed_model_values(nested, parent_key)


def parse_models_output(text: str) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return unique(listed_model_values(json.loads(stripped)))
    except json.JSONDecodeError:
        candidates = []
        for line in stripped.splitlines():
            match = re.match(
                r"^\s*(?:[-*]|\d+[.)])\s*([A-Za-z0-9][A-Za-z0-9._:/+-]{1,99})(?:\s|$)",
                line,
            )
            if not match:
                match = re.match(
                    r"^\s*([A-Za-z0-9][A-Za-z0-9._:/+-]{1,99})\s*(?:\(current\)|\(default\))?\s*$",
                    line,
                    re.I,
                )
            if match:
                candidates.append(match.group(1))
        return unique(candidates)


def discover_models_from_command(command: Sequence[str], help_text: str) -> List[str]:
    if "model" not in help_text.lower():
        return []
    attempts = [
        ["models", "--json"],
        ["models", "list", "--json"],
        ["model", "list", "--json"],
        ["--list-models", "--json"],
        ["models"],
    ]
    for arguments in attempts:
        code, output = run([*command, *arguments], timeout=6)
        if code == 0:
            models = parse_models_output(output)
            if models:
                return models
    return []


def configuration_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    count = 0
    for path in root.rglob("*"):
        if count >= 200:
            break
        if not path.is_file() or any(part.lower() in SKIP_DIRECTORIES for part in path.parts):
            continue
        name = path.name.lower()
        if path.suffix.lower() not in {".json", ".jsonc", ".yaml", ".yml", ".toml"}:
            continue
        if not any(marker in name for marker in ("config", "setting", "model", "profile")):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        count += 1
        yield path


def discover_models_from_files(root: Path) -> Tuple[List[str], Optional[str]]:
    models: List[str] = []
    defaults: List[str] = []
    assignment = re.compile(
        r"(?im)^\s*(default_?model|model|model_?id)\s*[:=]\s*[\"']?([^\"'#,\s]+)"
    )
    for path in configuration_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            payload = json.loads(text)
            models.extend(model_values(payload))
            defaults.extend(default_model_values(payload))
        except json.JSONDecodeError:
            for key, value in assignment.findall(text):
                models.append(value)
                if "default" in key.lower():
                    defaults.append(value)
    discovered = unique(models)
    default_candidates = unique(defaults)
    default_model = default_candidates[0] if default_candidates else None
    return discovered, default_model


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path.home() / ".zcode"))
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    command = find_command(root)
    warnings: List[str] = []
    help_text = ""
    arguments: List[str] = []
    prompt_via_stdin = True
    command_models: List[str] = []

    if command:
        _, help_text = run([*command, "--help"])
        if help_text:
            arguments, prompt_via_stdin, argument_warnings = discover_arguments(help_text)
            warnings.extend(argument_warnings)
            command_models = discover_models_from_command(command, help_text)
        else:
            warnings.append("ZCode was found, but zcode --help returned no usable output.")
    else:
        warnings.append(f"ZCode executable was not found under {root} or on PATH.")

    file_models, default_model = discover_models_from_files(root)
    models = unique([*command_models, *file_models])
    if default_model and default_model not in models:
        models.insert(0, default_model)

    print(json.dumps({
        "root": str(root),
        "command": command,
        "arguments": arguments,
        "prompt_via_stdin": prompt_via_stdin,
        "models": models,
        "default_model": default_model,
        "warnings": warnings,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
