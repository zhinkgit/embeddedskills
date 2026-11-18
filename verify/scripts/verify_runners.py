"""verify 的执行器：host 单元测试、串口黑盒冒烟、JUnit 解析与报告。

本模块不依赖仓库里的其他 skill 进程，只复用它们的配置与命令行约定：
- host 单测：执行计划里的 `unit.command`，或自动探测 CTest / pytest
- 冒烟：通过 pyserial 打开串口，按步骤发送/等待/断言
- 复位：调用 jlink / openocd / probe-rs 的 CLI，或计划里的自定义命令
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from verify_plan import (
    PlanError,
    resolve_serial_settings,
    resolve_unit_settings,
)
from verify_runtime import (
    get_state_entry,
    is_missing,
    load_full_project_config,
    load_workspace_state,
    report_dir,
    run_process,
    tail,
    workspace_root,
)


ROOT_DIR = Path(__file__).resolve().parents[2]


class RunnerError(RuntimeError):
    """测试基础设施错误：串口打不开、工具缺失、计划无法执行。"""

    def __init__(self, message: str, code: str = "tool_failed"):
        super().__init__(message)
        self.code = code


class StepFailure(AssertionError):
    """单个测试步骤失败；属于测试结果，不是基础设施错误。"""

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


# ---------------------------------------------------------------------------
# JUnit
# ---------------------------------------------------------------------------


def _tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def parse_junit_xml(path: str | Path) -> dict:
    """解析 JUnit XML，返回 totals 与用例列表。"""
    file_path = Path(path)
    try:
        root = ET.parse(file_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RunnerError(f"JUnit XML 无法解析：{file_path}（{exc}）") from exc

    cases: list[dict] = []
    for element in root.iter():
        if _tag(element) != "testcase":
            continue
        status = "passed"
        message = ""
        for child in element:
            child_tag = _tag(child)
            if child_tag == "skipped":
                status = "skipped"
            elif child_tag == "failure":
                status = "failed"
            elif child_tag == "error":
                status = "error"
            if status in ("failed", "error", "skipped"):
                message = child.get("message") or (child.text or "").strip()
                break
        try:
            duration = float(element.get("time") or 0)
        except ValueError:
            duration = 0.0
        cases.append(
            {
                "name": element.get("name") or "",
                "classname": element.get("classname") or "",
                "status": status,
                "duration_ms": int(duration * 1000),
                "message": message[:1000],
            }
        )

    failures = sum(1 for case in cases if case["status"] == "failed")
    errors = sum(1 for case in cases if case["status"] == "error")
    skipped = sum(1 for case in cases if case["status"] == "skipped")
    passed = sum(1 for case in cases if case["status"] == "passed")
    return {
        "available": True,
        "path": str(file_path.resolve()),
        "totals": {
            "tests": len(cases),
            "passed": passed,
            "failed": failures,
            "errors": errors,
            "skipped": skipped,
        },
        "cases": cases[:200],
    }


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def detect_unit_command(workspace: str | Path, junit_path: str | Path) -> tuple[list[str], str]:
    """自动探测 host 单测命令，返回 (command, framework)。"""
    root = Path(workspace)
    junit = str(Path(junit_path))
    build_dir = root / "build"
    if (build_dir / "CTestTestfile.cmake").is_file():
        return ["ctest", "--test-dir", str(build_dir), "--output-on-failure", "--output-junit", junit], "ctest"
    if (root / "CTestTestfile.cmake").is_file():
        return ["ctest", "--test-dir", str(root), "--output-on-failure", "--output-junit", junit], "ctest"
    pytest_markers = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
    if any((root / marker).is_file() for marker in pytest_markers) and (root / "tests").is_dir():
        return [sys.executable, "-m", "pytest", "-q", "--junitxml", junit], "pytest"
    raise RunnerError("未配置 unit.command，且未发现 CTest / pytest 工程；请在测试计划中提供 unit.command")


def run_unit(
    plan: dict,
    workspace: str | Path,
    *,
    cli_junit: str | None = None,
    dry_run: bool = False,
) -> dict:
    root = Path(workspace)
    settings = resolve_unit_settings(plan, str(root), cli_junit)
    command = settings["command"]
    framework = "custom"
    if not command:
        command, framework = detect_unit_command(root, settings["junit"])
    working_dir = Path(settings["working_dir"])
    if not working_dir.is_dir():
        raise RunnerError(f"unit.working_dir 不存在：{working_dir}")

    junit_path = Path(settings["junit"])
    if dry_run:
        return {
            "passed": None,
            "dry_run": True,
            "framework": framework,
            "command": command,
            "working_dir": str(working_dir),
            "junit": str(junit_path),
        }

    junit_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        junit_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RunnerError(f"无法清理旧的 JUnit 文件：{junit_path}（{exc}）") from exc

    started = time.time()
    process = run_process(command, cwd=working_dir, timeout_sec=settings["timeout_sec"])
    duration_ms = int((time.time() - started) * 1000)

    junit_info: dict = {"available": False, "path": str(junit_path), "totals": {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}, "cases": []}
    if junit_path.is_file():
        junit_info = parse_junit_xml(junit_path)

    totals = junit_info.get("totals") or {}
    failures = int(totals.get("failed", 0)) + int(totals.get("errors", 0))
    timed_out = bool(process.get("timed_out"))
    exit_code = int(process.get("exit_code", 1))
    passed = (not timed_out) and exit_code == 0 and failures == 0
    if junit_info.get("available") and failures > 0:
        passed = False

    error = None
    if timed_out:
        error = {"code": "timeout", "message": f"unit 测试超时（{settings['timeout_sec']}s）"}
    elif process.get("error"):
        error = process["error"]
    elif not passed:
        error = {"code": "unit_failed", "message": f"unit 测试失败（exit={exit_code}, failures={failures}）"}

    return {
        "passed": passed,
        "framework": framework,
        "command": command,
        "working_dir": str(working_dir),
        "junit": junit_info,
        "totals": totals,
        "cases": junit_info.get("cases", []),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout_tail": tail(process.get("stdout", "")),
        "stderr_tail": tail(process.get("stderr", "")),
        "error": error,
    }


# ---------------------------------------------------------------------------
# Serial transport
# ---------------------------------------------------------------------------


class SerialTransport:
    """pyserial 的最小封装，便于在测试中注入 FakeTransport。"""

    def __init__(self, config: dict):
        self.config = config
        self.ser = None

    def open(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RunnerError("pyserial 未安装，请执行 pip install pyserial") from exc
        port = self.config.get("port")
        if is_missing(port):
            raise RunnerError("未配置串口：请在 smoke.serial.port 或 .embeddedskills/config.json 的 serial.port 中指定")
        parity_map = {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=int(self.config["baudrate"]),
                bytesize=int(self.config.get("bytesize", 8)),
                parity=parity_map.get(self.config.get("parity", "none"), "N"),
                stopbits=self.config.get("stopbits", 1),
                timeout=min(float(self.config.get("timeout_sec", 1.0)), 0.2),
            )
        except Exception as exc:  # pyserial raises SerialException / OSError
            raise RunnerError(f"串口打开失败：{port}（{exc}）", code="serial_open_failed") from exc

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def write(self, payload: bytes) -> None:
        if self.ser is None:
            raise RunnerError("串口尚未打开")
        try:
            self.ser.write(payload)
            self.ser.flush()
        except Exception as exc:
            raise RunnerError(f"串口写入失败（{exc}）") from exc

    def flush_input(self) -> None:
        if self.ser is None:
            raise RunnerError("串口尚未打开")
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

    def read_until(self, pattern: re.Pattern, timeout_sec: float, encoding: str) -> tuple[bool, str, str]:
        if self.ser is None:
            raise RunnerError("串口尚未打开")
        deadline = time.monotonic() + timeout_sec
        buffer = ""
        while time.monotonic() < deadline:
            try:
                waiting = int(getattr(self.ser, "in_waiting", 0) or 0)
                chunk = self.ser.read(max(1, waiting))
            except Exception as exc:
                raise RunnerError(f"串口读取失败（{exc}）") from exc
            if chunk:
                buffer += chunk.decode(encoding, errors="replace")
                match = pattern.search(buffer)
                if match:
                    return True, buffer, match.group(0)
            else:
                time.sleep(0.01)
        match = pattern.search(buffer)
        if match:
            return True, buffer, match.group(0)
        return False, buffer, ""


# ---------------------------------------------------------------------------
# Smoke execution
# ---------------------------------------------------------------------------


def _substitute_command(command: list[str], workspace: Path) -> list[str]:
    return [token.replace("{workspace}", str(workspace)) for token in command]


def _infer_reset_backend(full_config: dict) -> str:
    if (full_config.get("jlink") or {}).get("device"):
        return "jlink"
    openocd = full_config.get("openocd") or {}
    if openocd.get("board") or (openocd.get("interface") and openocd.get("target")):
        return "openocd"
    if (full_config.get("probe-rs") or {}).get("chip"):
        return "probe-rs"
    return ""


def build_reset_command(step: dict, smoke_reset: dict, workspace: Path, full_config: dict) -> list[str] | None:
    step = step or {}
    if step.get("command"):
        return _substitute_command(list(step["command"]), workspace)
    merged = {**(smoke_reset or {}), **step}
    if not step.get("backend") and merged.get("command"):
        return _substitute_command(list(merged["command"]), workspace)

    backend = merged.get("backend") or _infer_reset_backend(full_config)
    if not backend or backend == "none":
        if backend == "none":
            return None
        raise RunnerError("reset 步骤未配置 backend，且工程中没有 jlink/openocd/probe-rs 的复位配置")

    if backend == "jlink":
        jlink = full_config.get("jlink") or {}
        device = merged.get("device") or jlink.get("device")
        if is_missing(device):
            raise RunnerError("jlink 复位需要 device：请在 jlink.device 或 reset.device 中配置")
        command = [
            sys.executable,
            str(ROOT_DIR / "jlink" / "scripts" / "jlink_exec.py"),
            "reset",
            "--workspace",
            str(workspace),
            "--json",
            "--device",
            str(device),
        ]
        if jlink.get("interface"):
            command.extend(["--interface", str(jlink["interface"])])
        if jlink.get("speed"):
            command.extend(["--speed", str(jlink["speed"])])
        return command

    if backend == "openocd":
        command = [
            sys.executable,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_run.py"),
            "reset",
            "--workspace",
            str(workspace),
            "--json",
        ]
        if merged.get("mode"):
            command.extend(["--mode", str(merged["mode"])])
        return command

    if backend == "probe-rs":
        executable = shutil.which("probe-rs")
        if not executable:
            raise RunnerError("未找到 probe-rs 可执行文件")
        chip = merged.get("device") or (full_config.get("probe-rs") or {}).get("chip")
        if is_missing(chip):
            raise RunnerError("probe-rs 复位需要 chip：请在 probe-rs.chip 或 reset.device 中配置")
        return [executable, "reset", "--chip", str(chip)]

    raise RunnerError(f"不支持的 reset backend：{backend}")


def _run_reset(step: dict, smoke_reset: dict, workspace: Path, full_config: dict) -> str:
    command = build_reset_command(step, smoke_reset, workspace, full_config)
    if command is None:
        return "skipped"
    result = run_process(command, cwd=workspace, timeout_sec=30)
    if result.get("error"):
        if (result["error"] or {}).get("code") == "command_not_found":
            raise RunnerError(result["error"]["message"])
        raise StepFailure(f"复位失败：{result['error']['message']}", {"command": command})
    if result["exit_code"] != 0:
        raise StepFailure(f"复位命令退出码 {result['exit_code']}：{tail(result.get('stderr', ''), 500)}", {"command": command})
    output = (result.get("stdout") or "").strip()
    if output.startswith("{"):
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("status") == "error":
            message = (payload.get("error") or {}).get("message") or "复位命令返回错误"
            raise StepFailure(f"复位失败：{message}", {"command": command})
    return "ok"


def _build_send_payload(step: dict, encoding: str) -> bytes:
    data = step.get("data", "")
    if step.get("hex"):
        try:
            return bytes.fromhex(str(data).replace(" ", "").replace("0x", "").replace(",", ""))
        except ValueError as exc:
            raise PlanError(f"serial_send.hex 数据非法：{data}") from exc
    payload = str(data).encode(step.get("encoding") or encoding, errors="replace")
    ending = step.get("line_ending", "none")
    if ending == "cr":
        payload += b"\r"
    elif ending == "lf":
        payload += b"\n"
    elif ending == "crlf":
        payload += b"\r\n"
    return payload


def _step_label(step_type: str, step: dict) -> str:
    if step_type == "serial_expect":
        return f"serial_expect {step.get('regex') or step.get('contains')}"
    if step_type == "serial_send":
        return f"serial_send {step.get('data', '')!r}"
    if step_type == "reset":
        return "reset"
    if step_type == "sleep":
        return f"sleep {step}"
    if step_type == "run":
        return "run " + " ".join(step.get("command") or [])
    return step_type


def execute_smoke(
    plan: dict,
    workspace: str | Path,
    *,
    transport_factory: Callable[[dict], Any] | None = None,
    only_case: str | None = None,
    dry_run: bool = False,
    cli_port: str | None = None,
    cli_baudrate: int | None = None,
) -> dict:
    root = Path(workspace)
    full_config = load_full_project_config(str(root))
    settings, sources = resolve_serial_settings(plan, str(root), cli_port=cli_port, cli_baudrate=cli_baudrate)
    smoke = plan.get("smoke") or {}
    smoke_reset = smoke.get("reset") or {}
    cases = list(smoke.get("cases") or [])
    if only_case:
        cases = [case for case in cases if case.get("name") == only_case]
        if not cases:
            raise RunnerError(f"未找到用例：{only_case}")

    if dry_run:
        return {
            "passed": None,
            "dry_run": True,
            "serial": settings,
            "parameter_sources": sources,
            "cases": [
                {
                    "name": case.get("name", ""),
                    "steps": [
                        {"type": next(iter(step)), "detail": _step_label(next(iter(step)), step[next(iter(step))] or {})}
                        for step in case.get("steps") or []
                    ],
                }
                for case in cases
            ],
        }

    if is_missing(settings.get("port")):
        raise RunnerError("未配置串口：请在 smoke.serial.port、.embeddedskills/config.json 的 verify.serial / serial.port 中指定")

    factory = transport_factory or SerialTransport
    transport = factory(settings)
    transport.open()
    started = time.time()
    case_results: list[dict] = []
    failed_cases = 0
    try:
        for case in cases:
            case_started = time.time()
            case_result = {
                "name": case.get("name", ""),
                "status": "passed",
                "duration_ms": 0,
                "steps": [],
                "error": None,
                "log": "",
            }
            log_parts: list[str] = []
            case_deadline = case_started + float(case.get("timeout_sec") or 0) if case.get("timeout_sec") else None
            try:
                for step in case.get("steps") or []:
                    step_type = next(iter(step))
                    payload = step[step_type] or {}
                    step_started = time.time()
                    step_record = {"type": step_type, "status": "passed", "duration_ms": 0, "detail": {}}
                    try:
                        if step_type == "reset":
                            _run_reset(payload, smoke_reset, root, full_config)
                            wait_ms = int(payload.get("wait_ms") or smoke_reset.get("wait_ms") or 0)
                            if wait_ms:
                                time.sleep(wait_ms / 1000)
                            log_parts.append("[reset] ok")
                            step_record["detail"] = {"backend": payload.get("backend") or smoke_reset.get("backend") or "auto"}
                        elif step_type == "sleep":
                            ms = payload.get("ms")
                            seconds = payload.get("seconds")
                            duration = (float(seconds) if seconds is not None else float(ms or 0) / 1000)
                            time.sleep(duration)
                            log_parts.append(f"[sleep] {duration:.3f}s")
                        elif step_type == "serial_send":
                            payload_bytes = _build_send_payload(payload, settings.get("encoding", "utf-8"))
                            transport.write(payload_bytes)
                            log_parts.append(f"[send] {payload_bytes!r}")
                            step_record["detail"] = {"bytes": len(payload_bytes)}
                            delay_ms = int(payload.get("delay_ms") or 0)
                            if delay_ms:
                                time.sleep(delay_ms / 1000)
                        elif step_type == "serial_flush":
                            transport.flush_input()
                            log_parts.append("[flush]")
                        elif step_type == "serial_expect":
                            pattern_text = payload.get("regex") or payload.get("contains")
                            pattern = re.compile(pattern_text) if payload.get("regex") else re.compile(re.escape(pattern_text))
                            timeout = float(payload.get("timeout_sec") or 5.0)
                            matched, buffer, matched_text = transport.read_until(pattern, timeout, settings.get("encoding", "utf-8"))
                            if not matched:
                                raise StepFailure(
                                    f"等待串口输出超时（{timeout:.1f}s）：{pattern_text}；已收到：{buffer[-1000:]!r}",
                                    {"received": buffer[-2000:]},
                                )
                            log_parts.append(f"[expect] {matched_text!r}")
                            step_record["detail"] = {"matched": matched_text}
                        elif step_type == "run":
                            command = _substitute_command(list(payload.get("command") or []), root)
                            cwd = Path(payload.get("cwd") or root)
                            if not cwd.is_absolute():
                                cwd = root / cwd
                            expected = int(payload.get("expect_exit_code", 0))
                            result = run_process(command, cwd=cwd, timeout_sec=payload.get("timeout_sec"))
                            if result.get("error"):
                                raise StepFailure(f"run 失败：{result['error']['message']}", {"command": command})
                            if result["exit_code"] != expected:
                                raise StepFailure(
                                    f"run 退出码 {result['exit_code']}，期望 {expected}：{tail(result.get('stderr', ''), 500)}",
                                    {"command": command},
                                )
                            log_parts.append(f"[run] exit={result['exit_code']}")
                        else:
                            raise RunnerError(f"未知步骤类型：{step_type}")
                    except StepFailure as exc:
                        step_record["status"] = "failed"
                        step_record["detail"] = {**(step_record.get("detail") or {}), **(exc.detail or {}), "message": str(exc)}
                        step_record["duration_ms"] = int((time.time() - step_started) * 1000)
                        case_result["steps"].append(step_record)
                        raise
                    step_record["duration_ms"] = int((time.time() - step_started) * 1000)
                    case_result["steps"].append(step_record)
                    if case_deadline and time.time() > case_deadline:
                        raise StepFailure(f"用例超时（{case.get('timeout_sec')}s）")
            except StepFailure as exc:
                case_result["status"] = "failed"
                case_result["error"] = str(exc)
                failed_cases += 1
                if smoke.get("fail_fast"):
                    log_parts.append(f"[failed] {exc}")
                    case_result["log"] = "\n".join(log_parts)
                    case_result["duration_ms"] = int((time.time() - case_started) * 1000)
                    case_results.append(case_result)
                    break
            finally:
                case_result["duration_ms"] = int((time.time() - case_started) * 1000)
                case_result["log"] = "\n".join(log_parts)
            case_results.append(case_result)
    finally:
        try:
            transport.close()
        except Exception:
            pass

    duration_ms = int((time.time() - started) * 1000)
    return {
        "passed": failed_cases == 0,
        "serial": {"port": settings.get("port"), "baudrate": settings.get("baudrate")},
        "parameter_sources": sources,
        "total": len(case_results),
        "passed_cases": len(case_results) - failed_cases,
        "failed_cases": failed_cases,
        "cases": case_results,
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def scan_workspace(workspace: str | Path) -> dict:
    root = Path(workspace)
    plan_path = None
    for relative in (".embeddedskills/verify/plan.json", ".embeddedskills/verify.json", "verify.plan.json"):
        candidate = root / relative
        if candidate.is_file():
            plan_path = str(candidate.resolve())
            break

    frameworks = []
    if (root / "build" / "CTestTestfile.cmake").is_file() or (root / "CTestTestfile.cmake").is_file():
        frameworks.append("ctest")
    if any((root / marker).is_file() for marker in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")) and (root / "tests").is_dir():
        frameworks.append("pytest")
    if (root / "project.yml").is_file() or (root / "project.yaml").is_file():
        frameworks.append("ceedling")

    ports = []
    try:
        from serial.tools import list_ports

        ports = [{"port": port.device, "description": port.description or ""} for port in list_ports.comports()]
    except ImportError:
        ports = []
    except Exception:
        ports = []

    tools = {name: bool(shutil.which(name)) for name in ("ctest", "pytest", "probe-rs", "openocd")}
    state = load_workspace_state(str(root))
    return {
        "workspace": str(root.resolve()),
        "plan": plan_path,
        "unit_frameworks": frameworks,
        "serial_ports": ports,
        "tools": tools,
        "state": {
            "last_build": get_state_entry(state, "last_build"),
            "last_flash": get_state_entry(state, "last_flash"),
            "last_verify": get_state_entry(state, "last_verify"),
        },
        "report_dir": str(report_dir(str(root))),
    }