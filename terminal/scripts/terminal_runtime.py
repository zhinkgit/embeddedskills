from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


class TerminalError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def result(action: str, summary: str, **details) -> dict:
    return {"status": "ok", "action": action, "summary": summary, "details": details}


def failure(action: str, code: str, message: str, **details) -> dict:
    return {
        "status": "error", "action": action, "summary": message, "details": details,
        "error": {"code": code, "message": message},
    }


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise TerminalError("invalid_arguments", message)


def read_mapping(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TerminalError("invalid_config", f"应为 JSON 对象：{path}")
    return value


def settings(args) -> tuple[dict, dict]:
    local = read_mapping(Path(__file__).resolve().parents[1] / "config.json")
    project = read_mapping(args.workspace / ".embeddedskills" / "config.json").get("terminal", {})
    if not isinstance(project, dict):
        raise TerminalError("invalid_config", "工程配置 terminal 应为 JSON 对象")
    return local, project


def executable(name: str, override: str | None = None) -> str:
    candidate = override or name
    found = shutil.which(candidate)
    if found is None:
        raise TerminalError("tool_not_found", f"未找到可执行文件：{candidate}", tool=name)
    return found


def emit(payload: dict, as_json: bool) -> int:
    if as_json:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(payload["summary"], file=sys.stderr if payload["status"] == "error" else sys.stdout)
        if payload["details"]:
            print(json.dumps(payload["details"], ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1
