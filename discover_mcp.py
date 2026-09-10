from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def candidate_paths() -> List[Path]:
    home = Path.home()
    return [
        home / ".pi" / "agent" / "mcp.json",
        home / ".pi" / "mcp.json",
        home / ".config" / "mcp" / "mcp.json",
        home / ".mcp.json",
        Path.cwd() / ".pi" / "mcp.json",
        Path.cwd() / ".mcp.json",
    ]


def discover() -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen = set()
    for path in candidate_paths():
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = payload.get("mcpServers", {})
        if not isinstance(servers, dict):
            continue
        for name, server in servers.items():
            if not isinstance(server, dict):
                continue
            transport = "stdio" if server.get("command") else "http" if server.get("url") else "unknown"
            found.append({
                "config_path": str(resolved),
                "server": str(name),
                "transport": transport,
                "likely_welink": "welink" in str(name).lower(),
            })
    return found


if __name__ == "__main__":
    print(json.dumps({"servers": discover()}, ensure_ascii=False))
