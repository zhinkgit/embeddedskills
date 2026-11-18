"""测试计划解析与校验。

计划是版本化的测试资产，默认放在 `.embeddedskills/verify/plan.json`。
本模块只做纯逻辑校验，不接触串口、探针或子进程，便于单元测试。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from verify_runtime import (
    ConfigError,
    is_missing,
    load_full_project_config,
    load_json_file,
    load_local_config,
    load_project_config,
    workspace_root,
)


STEP_TYPES = ("reset", "sleep", "serial_send", "serial_expect", "serial_flush", "run")
LINE_ENDINGS = ("none", "cr", "lf", "crlf")
RESET_BACKENDS = ("jlink", "openocd", "probe-rs", "none")


class PlanError(ValueError):
    """测试计划缺失、结构错误或字段非法。"""


def load_plan(path: str | Path) -> dict:
    file_path = Path(path)
    try:
        plan = load_json_file(file_path, required=True)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    validate_plan(plan)
    return plan


def _expect_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise PlanError(f"{label} 必须是 JSON 对象")
    return value


def _positive_number(value: Any, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError(f"{label} 必须是数字")
    if allow_zero and value < 0:
        raise PlanError(f"{label} 不能为负数")
    if not allow_zero and value <= 0:
        raise PlanError(f"{label} 必须大于 0")
    return float(value)


def _optional_string(container: dict, key: str, label: str) -> str:
    value = container.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PlanError(f"{label}.{key} 必须是字符串")
    return value


def _command_list(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        raise PlanError(f"{label} 必须是字符串数组，例如 [\"ctest\", \"--test-dir\", \"build\"]")
    if not isinstance(value, list) or not value:
        raise PlanError(f"{label} 必须是非空字符串数组")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise PlanError(f"{label}[{index}] 必须是非空字符串")
        result.append(item)
    return result


def validate_plan(plan: dict) -> None:
    if not isinstance(plan, dict):
        raise PlanError("测试计划顶层必须是 JSON 对象")

    known_top = {"name", "description", "version", "$schema", "unit", "smoke"}
    unknown = sorted(set(plan) - known_top)
    if unknown:
        raise PlanError(f"测试计划包含未知字段：{', '.join(unknown)}")

    has_unit = "unit" in plan and plan["unit"] is not None
    has_smoke = "smoke" in plan and plan["smoke"] is not None
    if not has_unit and not has_smoke:
        raise PlanError("测试计划至少需要 unit 或 smoke 其中一段")

    _optional_string(plan, "name", "plan")
    _optional_string(plan, "description", "plan")

    if has_unit:
        validate_unit(_expect_object(plan["unit"], "unit"))
    if has_smoke:
        validate_smoke(_expect_object(plan["smoke"], "smoke"))


def validate_unit(unit: dict) -> None:
    known = {"command", "working_dir", "timeout_sec", "junit"}
    unknown = sorted(set(unit) - known)
    if unknown:
        raise PlanError(f"unit 包含未知字段：{', '.join(unknown)}")

    if "command" in unit:
        _command_list(unit["command"], "unit.command")
    if "working_dir" in unit:
        _optional_string(unit, "working_dir", "unit")
    if "junit" in unit:
        _optional_string(unit, "junit", "unit")
    if "timeout_sec" in unit:
        _positive_number(unit["timeout_sec"], "unit.timeout_sec")


def validate_smoke(smoke: dict) -> None:
    known = {"serial", "reset", "cases", "fail_fast"}
    unknown = sorted(set(smoke) - known)
    if unknown:
        raise PlanError(f"smoke 包含未知字段：{', '.join(unknown)}")

    if "serial" in smoke:
        validate_serial(_expect_object(smoke["serial"], "smoke.serial"))

    if "reset" in smoke:
        validate_reset(_expect_object(smoke["reset"], "smoke.reset"), "smoke.reset")

    if not isinstance(smoke.get("fail_fast", False), bool):
        raise PlanError("smoke.fail_fast 必须是布尔值")

    cases = smoke.get("cases")
    if not isinstance(cases, list) or not cases:
        raise PlanError("smoke.cases 必须是非空数组")
    names = set()
    for index, case in enumerate(cases):
        validate_case(_expect_object(case, f"smoke.cases[{index}]"), f"smoke.cases[{index}]")
        name = case.get("name")
        if name in names:
            raise PlanError(f"smoke.cases 中存在重复用例名：{name}")
        names.add(name)


def validate_serial(serial: dict) -> None:
    known = {"port", "baudrate", "bytesize", "parity", "stopbits", "encoding", "timeout_sec"}
    unknown = sorted(set(serial) - known)
    if unknown:
        raise PlanError(f"smoke.serial 包含未知字段：{', '.join(unknown)}")

    if "port" in serial:
        _optional_string(serial, "port", "smoke.serial")
    if "encoding" in serial:
        _optional_string(serial, "encoding", "smoke.serial")
    if "baudrate" in serial and (isinstance(serial["baudrate"], bool) or not isinstance(serial["baudrate"], int) or serial["baudrate"] <= 0):
        raise PlanError("smoke.serial.baudrate 必须是正整数")
    if "bytesize" in serial and serial["bytesize"] not in (5, 6, 7, 8):
        raise PlanError("smoke.serial.bytesize 必须是 5/6/7/8")
    if "parity" in serial and serial["parity"] not in ("none", "even", "odd", "mark", "space"):
        raise PlanError("smoke.serial.parity 必须是 none/even/odd/mark/space")
    if "stopbits" in serial and serial["stopbits"] not in (1, 1.5, 2):
        raise PlanError("smoke.serial.stopbits 必须是 1/1.5/2")
    if "timeout_sec" in serial:
        _positive_number(serial["timeout_sec"], "smoke.serial.timeout_sec")


def validate_case(case: dict, label: str) -> None:
    known = {"name", "description", "timeout_sec", "steps"}
    unknown = sorted(set(case) - known)
    if unknown:
        raise PlanError(f"{label} 包含未知字段：{', '.join(unknown)}")

    name = case.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlanError(f"{label}.name 必须是非空字符串")
    if "description" in case:
        _optional_string(case, "description", label)
    if "timeout_sec" in case:
        _positive_number(case["timeout_sec"], f"{label}.timeout_sec")

    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanError(f"{label}.steps 必须是非空数组")
    for index, step in enumerate(steps):
        validate_step(_expect_object(step, f"{label}.steps[{index}]"), f"{label}.steps[{index}]")


def validate_step(step: dict, label: str) -> None:
    if len(step) != 1:
        raise PlanError(f"{label} 必须只包含一个动作键，可选：{', '.join(STEP_TYPES)}")
    step_type = next(iter(step))
    if step_type not in STEP_TYPES:
        raise PlanError(f"{label} 未知动作 {step_type}，可选：{', '.join(STEP_TYPES)}")
    payload = step[step_type]
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise PlanError(f"{label}.{step_type} 必须是 JSON 对象")

    if step_type == "reset":
        validate_reset(payload, f"{label}.reset")
    elif step_type == "sleep":
        validate_sleep(payload, f"{label}.sleep")
    elif step_type == "serial_send":
        validate_serial_send(payload, f"{label}.serial_send")
    elif step_type == "serial_expect":
        validate_serial_expect(payload, f"{label}.serial_expect")
    elif step_type == "serial_flush":
        if payload:
            raise PlanError(f"{label}.serial_flush 不接受参数")
    elif step_type == "run":
        validate_run(payload, f"{label}.run")


def validate_reset(payload: dict, label: str) -> None:
    known = {"backend", "command", "device", "wait_ms", "mode"}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise PlanError(f"{label} 包含未知字段：{', '.join(unknown)}")

    if "backend" in payload and payload["backend"] not in RESET_BACKENDS:
        raise PlanError(f"{label}.backend 必须是 {'/'.join(RESET_BACKENDS)}")
    if "command" in payload:
        _command_list(payload["command"], f"{label}.command")
    if "device" in payload:
        _optional_string(payload, "device", label)
    if "mode" in payload:
        _optional_string(payload, "mode", label)
    if "wait_ms" in payload:
        wait_ms = payload["wait_ms"]
        if isinstance(wait_ms, bool) or not isinstance(wait_ms, int) or wait_ms < 0:
            raise PlanError(f"{label}.wait_ms 必须是非负整数")


def validate_sleep(payload: dict, label: str) -> None:
    known = {"ms", "seconds"}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise PlanError(f"{label} 包含未知字段：{', '.join(unknown)}")
    if len(payload) != 1:
        raise PlanError(f"{label} 必须且只能提供 ms 或 seconds")
    key = next(iter(payload))
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PlanError(f"{label}.{key} 必须是非负数字")


def validate_serial_send(payload: dict, label: str) -> None:
    known = {"data", "line_ending", "hex", "encoding", "delay_ms"}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise PlanError(f"{label} 包含未知字段：{', '.join(unknown)}")

    data = payload.get("data")
    if not isinstance(data, str):
        raise PlanError(f"{label}.data 必须是字符串")
    if "line_ending" in payload and payload["line_ending"] not in LINE_ENDINGS:
        raise PlanError(f"{label}.line_ending 必须是 {'/'.join(LINE_ENDINGS)}")
    if "hex" in payload and not isinstance(payload["hex"], bool):
        raise PlanError(f"{label}.hex 必须是布尔值")
    if "encoding" in payload:
        _optional_string(payload, "encoding", label)
    if "delay_ms" in payload:
        delay = payload["delay_ms"]
        if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
            raise PlanError(f"{label}.delay_ms 必须是非负整数")


def validate_serial_expect(payload: dict, label: str) -> None:
    known = {"regex", "contains", "timeout_sec", "encoding"}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise PlanError(f"{label} 包含未知字段：{', '.join(unknown)}")

    has_regex = "regex" in payload
    has_contains = "contains" in payload
    if has_regex == has_contains:
        raise PlanError(f"{label} 必须且只能提供 regex 或 contains 之一")
    pattern = payload["regex"] if has_regex else payload["contains"]
    if not isinstance(pattern, str) or not pattern:
        raise PlanError(f"{label}.{'regex' if has_regex else 'contains'} 必须是非空字符串")
    if has_regex:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise PlanError(f"{label}.regex 不是合法正则：{exc}") from exc
    if "timeout_sec" in payload:
        _positive_number(payload["timeout_sec"], f"{label}.timeout_sec")
    if "encoding" in payload:
        _optional_string(payload, "encoding", label)


def validate_run(payload: dict, label: str) -> None:
    known = {"command", "timeout_sec", "expect_exit_code", "cwd"}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise PlanError(f"{label} 包含未知字段：{', '.join(unknown)}")

    _command_list(payload.get("command"), f"{label}.command")
    if "timeout_sec" in payload:
        _positive_number(payload["timeout_sec"], f"{label}.timeout_sec")
    if "expect_exit_code" in payload:
        code = payload["expect_exit_code"]
        if isinstance(code, bool) or not isinstance(code, int):
            raise PlanError(f"{label}.expect_exit_code 必须是整数")
    if "cwd" in payload:
        _optional_string(payload, "cwd", label)


def resolve_unit_settings(plan: dict, workspace: str | None, cli_junit: str | None = None) -> dict:
    unit = plan.get("unit") or {}
    project_config = load_project_config(workspace)
    verify_config = project_config if isinstance(project_config, dict) else {}
    local_config = load_local_config(__file__)

    junit_value = (
        cli_junit
        or unit.get("junit")
        or verify_config.get("junit")
        or local_config.get("junit")
        or ".embeddedskills/reports/verify/unit.xml"
    )
    junit_path = Path(str(junit_value)).expanduser()
    if not junit_path.is_absolute():
        junit_path = workspace_root(workspace) / junit_path

    working_dir = Path(str(unit.get("working_dir") or ".")).expanduser()
    if not working_dir.is_absolute():
        working_dir = workspace_root(workspace) / working_dir

    timeout = (
        unit.get("timeout_sec")
        or verify_config.get("unit_timeout_sec")
        or local_config.get("unit_timeout_sec")
        or 600
    )
    command = unit.get("command")
    if command:
        command = [
            token.replace("{workspace}", str(workspace_root(workspace))).replace("{junit}", str(junit_path))
            for token in command
        ]
    return {
        "command": command,
        "working_dir": str(working_dir.resolve()),
        "timeout_sec": float(timeout),
        "junit": str(junit_path.resolve()),
    }


def resolve_serial_settings(
    plan: dict,
    workspace: str | None,
    *,
    cli_port: str | None = None,
    cli_baudrate: int | None = None,
) -> tuple[dict, dict]:
    smoke = plan.get("smoke") or {}
    plan_serial = smoke.get("serial") or {}
    full_config = load_full_project_config(workspace)
    verify_config = full_config.get("verify") if isinstance(full_config.get("verify"), dict) else {}
    verify_serial = verify_config.get("serial") if isinstance(verify_config.get("serial"), dict) else {}
    serial_config = full_config.get("serial") if isinstance(full_config.get("serial"), dict) else {}
    merged_config = {**serial_config, **verify_serial}

    sources: dict[str, str] = {}
    port = cli_port
    sources["port"] = "cli" if port else ""
    if is_missing(port):
        port = plan_serial.get("port")
        sources["port"] = "plan" if not is_missing(port) else ""
    if is_missing(port):
        port = merged_config.get("port")
        sources["port"] = "project_config:serial.port" if not is_missing(port) else ""

    baudrate = cli_baudrate
    sources["baudrate"] = "cli" if baudrate else ""
    if is_missing(baudrate):
        baudrate = plan_serial.get("baudrate")
        sources["baudrate"] = "plan" if not is_missing(baudrate) else ""
    if is_missing(baudrate):
        baudrate = merged_config.get("baudrate")
        sources["baudrate"] = "project_config:serial.baudrate" if not is_missing(baudrate) else ""
    if is_missing(baudrate):
        baudrate = 115200
        sources["baudrate"] = "default"

    bytesize = plan_serial.get("bytesize", merged_config.get("bytesize", 8))
    parity = plan_serial.get("parity", merged_config.get("parity", "none"))
    stopbits = plan_serial.get("stopbits", merged_config.get("stopbits", 1))
    encoding = plan_serial.get("encoding", merged_config.get("encoding", "utf-8"))
    timeout = plan_serial.get("timeout_sec", merged_config.get("timeout_sec", 1.0))

    return {
        "port": port,
        "baudrate": int(baudrate),
        "bytesize": int(bytesize),
        "parity": parity,
        "stopbits": stopbits,
        "encoding": encoding,
        "timeout_sec": float(timeout),
    }, {key: value for key, value in sources.items() if value}


def describe_plan(plan: dict) -> dict:
    unit = plan.get("unit") or {}
    smoke = plan.get("smoke") or {}
    cases = smoke.get("cases") or []
    return {
        "name": plan.get("name") or "",
        "unit_configured": "unit" in plan and plan.get("unit") is not None,
        "smoke_configured": "smoke" in plan and plan.get("smoke") is not None,
        "case_count": len(cases),
        "cases": [case.get("name", "") for case in cases],
        "step_count": sum(len(case.get("steps") or []) for case in cases),
    }