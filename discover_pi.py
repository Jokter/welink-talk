from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Sequence, Tuple


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def run(command: Sequence[str], timeout: int = 30) -> Tuple[int, str]:
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
        output = ANSI.sub("", (result.stdout + "\n" + result.stderr)).strip()
        return result.returncode, output
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def parse_models(output: str) -> List[str]:
    models: List[str] = []
    seen = set()
    for line in output.splitlines():
        columns = re.split(r"\s{2,}", line.strip())
        if len(columns) < 2:
            continue
        provider, model = columns[0], columns[1]
        if provider.lower() == "provider" and model.lower() == "model":
            continue
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", provider):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{1,199}", model):
            continue
        full_name = model if "/" in model else f"{provider}/{model}"
        lowered = full_name.lower()
        if lowered not in seen:
            seen.add(lowered)
            models.append(full_name)
    return models


def read_default_model() -> str:
    agent_dir = Path(
        os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    ).expanduser()
    settings_path = agent_dir / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    provider = settings.get("defaultProvider")
    model = settings.get("defaultModel")
    if not isinstance(model, str) or not model:
        return ""
    if isinstance(provider, str) and provider and "/" not in model:
        return f"{provider}/{model}"
    return model


def main() -> int:
    command = shutil.which("pi")
    if not command:
        print(json.dumps({
            "command": [],
            "models": [],
            "default_model": "",
            "version": "",
            "error": "The pi command was not found on PATH.",
        }))
        return 0

    command_path = str(Path(command).resolve())
    _, version = run([command_path, "--version"], timeout=10)
    list_code, model_output = run([command_path, "--list-models"], timeout=60)
    models = parse_models(model_output) if list_code == 0 else []
    default_model = read_default_model()
    if default_model and default_model not in models:
        models.insert(0, default_model)

    error = ""
    if list_code != 0:
        error = "pi --list-models failed. Start pi once and complete /login."
    elif not models:
        error = "No authenticated Pi models were found. Start pi and complete /login."

    print(json.dumps({
        "command": [command_path],
        "models": models,
        "default_model": default_model,
        "version": version.splitlines()[0] if version else "",
        "error": error,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
