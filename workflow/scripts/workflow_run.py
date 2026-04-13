"""workflow 薄编排执行层。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from workflow_runtime import (  # noqa: E402
    get_state_entry,
    load_json_file,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    workspace_root,
)


PYTHON_EXE = sys.executable


def discover_projects(root: Path) -> dict:
    return {
        "keil": sorted(str(path.resolve()) for path in root.rglob("*.uvprojx")),
        "gcc": sorted(str(path.parent.resolve()) for path in root.rglob("CMakePresets.json")),
    }


def _single_or_error(items: list[str], label: str) -> tuple[str | None, dict | None]:
    if len(items) == 1:
        return items[0], None
    if len(items) > 1:
        return None, {"code": "multiple_candidates", "message": f"发现多个{label}，请在配置或命令中显式指定", "candidates": items}
    return None, {"code": "not_found", "message": f"未发现可用的{label}", "candidates": []}


def run_json(cmd: list[str], workdir: Path) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(workdir), encoding="utf-8", errors="replace")
    payload = (proc.stdout or proc.stderr).strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "action": "subprocess",
            "error": {"code": "invalid_json", "message": payload[-500:] or "子进程未返回 JSON"},
        }


def select_build_backend(config: dict, discovery: dict, explicit: str | None) -> tuple[str | None, dict | None]:
    backend = explicit or config.get("preferred_build") or "auto"
    if backend != "auto":
        return backend, None
    candidates = [name for name in ("keil", "gcc") if discovery[name]]
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, {"code": "multiple_build_backends", "message": "同时发现 Keil 和 GCC 工程，请显式指定 build backend", "candidates": candidates}
    return None, {"code": "no_build_backend", "message": "未发现可构建工程", "candidates": []}


def build_project(workspace: Path, config: dict, discovery: dict, backend: str | None) -> dict:
    selected, error = select_build_backend(config, discovery, backend)
    if error:
        return {"status": "error", "action": "build", "error": error}

    if selected == "keil":
        project = config.get("keil", {}).get("project")
        if not project:
            project, error = _single_or_error(discovery["keil"], "Keil 工程")
            if error:
                return {"status": "error", "action": "build", "error": error}
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "keil" / "scripts" / "keil_build.py"),
            "build",
            "--workspace",
            str(workspace),
            "--project",
            project,
            "--json",
        ]
        target = config.get("keil", {}).get("target")
        uv4_exe = config.get("keil", {}).get("uv4_exe")
        if target:
            cmd.extend(["--target", target])
        if uv4_exe:
            cmd.extend(["--uv4", uv4_exe])
        return run_json(cmd, workspace)

    project = config.get("gcc", {}).get("project")
    if not project:
        project, error = _single_or_error(discovery["gcc"], "GCC 工程")
        if error:
            return {"status": "error", "action": "build", "error": error}
    preset = config.get("gcc", {}).get("preset")
    if not preset:
        return {"status": "error", "action": "build", "error": {"code": "missing_preset", "message": "workflow 需要在 workflow/config.json 中为 GCC 配置 preset"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "gcc" / "scripts" / "gcc_build.py"),
        "build",
        "--workspace",
        str(workspace),
        "--project",
        project,
        "--preset",
        preset,
        "--json",
    ]
    cmake_exe = config.get("gcc", {}).get("cmake_exe")
    if cmake_exe:
        cmd.extend(["--cmake", cmake_exe])
    return run_json(cmd, workspace)


def flash_project(workspace: Path, config: dict, state: dict, explicit: str | None) -> dict:
    backend = explicit or config.get("preferred_flash") or "auto"
    last_build = get_state_entry(state, "last_build")
    artifacts = last_build.get("artifacts", {})
    flash_file = last_build.get("flash_file") or artifacts.get("flash_file")
    if not flash_file:
        return {"status": "error", "action": "flash", "error": {"code": "missing_last_build", "message": "未找到 last_build.flash_file，请先执行 workflow build"}}

    if backend in ("auto", "openocd"):
        openocd_cfg = config.get("openocd", {})
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_run.py"),
            "flash",
            "--workspace",
            str(workspace),
            "--file",
            flash_file,
            "--json",
        ]
        if openocd_cfg.get("board"):
            cmd.extend(["--board", openocd_cfg["board"]])
        if openocd_cfg.get("interface"):
            cmd.extend(["--interface", openocd_cfg["interface"]])
        if openocd_cfg.get("target"):
            cmd.extend(["--target", openocd_cfg["target"]])
        return run_json(cmd, workspace)

    jlink_cfg = config.get("jlink", {})
    if not jlink_cfg.get("device"):
        return {"status": "error", "action": "flash", "error": {"code": "missing_device", "message": "workflow 使用 jlink flash 时需要在 workflow/config.json 里提供 jlink.device"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "jlink" / "scripts" / "jlink_exec.py"),
        "flash",
        "--file",
        flash_file,
        "--device",
        jlink_cfg["device"],
        "--json",
    ]
    if jlink_cfg.get("interface"):
        cmd.extend(["--interface", jlink_cfg["interface"]])
    if jlink_cfg.get("speed"):
        cmd.extend(["--speed", str(jlink_cfg["speed"])])
    return run_json(cmd, workspace)


def debug_project(workspace: Path, config: dict, state: dict, explicit: str | None) -> dict:
    backend = explicit or config.get("preferred_debug") or "auto"
    last_build = get_state_entry(state, "last_build")
    artifacts = last_build.get("artifacts", {})
    debug_file = last_build.get("debug_file") or artifacts.get("debug_file")
    if not debug_file:
        return {"status": "error", "action": "build-debug", "error": {"code": "missing_last_build", "message": "未找到 last_build.debug_file，请先执行 workflow build"}}

    if backend in ("auto", "openocd"):
        openocd_cfg = config.get("openocd", {})
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_gdb.py"),
            "crash-report",
            "--workspace",
            str(workspace),
            "--elf",
            debug_file,
            "--json",
        ]
        if openocd_cfg.get("board"):
            cmd.extend(["--board", openocd_cfg["board"]])
        if openocd_cfg.get("interface"):
            cmd.extend(["--interface", openocd_cfg["interface"]])
        if openocd_cfg.get("target"):
            cmd.extend(["--target", openocd_cfg["target"]])
        if openocd_cfg.get("gdb_exe"):
            cmd.extend(["--gdb-exe", openocd_cfg["gdb_exe"]])
        return run_json(cmd, workspace)

    jlink_cfg = config.get("jlink", {})
    if not jlink_cfg.get("device"):
        return {"status": "error", "action": "build-debug", "error": {"code": "missing_device", "message": "workflow 使用 jlink gdb 时需要在 workflow/config.json 里提供 jlink.device"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "jlink" / "scripts" / "jlink_gdb.py"),
        "crash-report",
        "--workspace",
        str(workspace),
        "--elf",
        debug_file,
        "--device",
        jlink_cfg["device"],
        "--json",
    ]
    if jlink_cfg.get("interface"):
        cmd.extend(["--interface", jlink_cfg["interface"]])
    if jlink_cfg.get("speed"):
        cmd.extend(["--speed", str(jlink_cfg["speed"])])
    return run_json(cmd, workspace)


def observe_project(workspace: Path, config: dict, explicit: str | None) -> dict:
    backend = explicit or config.get("preferred_observe") or "auto"
    if backend in ("auto", "openocd"):
        openocd_cfg = config.get("openocd", {})
        cmd = [
            PYTHON_EXE,
            str(ROOT_DIR / "openocd" / "scripts" / "openocd_semihosting.py"),
            "--workspace",
            str(workspace),
            "--json",
        ]
        if openocd_cfg.get("board"):
            cmd.extend(["--board", openocd_cfg["board"]])
        if openocd_cfg.get("interface"):
            cmd.extend(["--interface", openocd_cfg["interface"]])
        if openocd_cfg.get("target"):
            cmd.extend(["--target", openocd_cfg["target"]])
        return {"status": "ok", "action": "observe", "summary": "已生成 openocd semihosting 观察命令", "details": {"command": cmd}}

    jlink_cfg = config.get("jlink", {})
    if not jlink_cfg.get("device"):
        return {"status": "error", "action": "observe", "error": {"code": "missing_device", "message": "workflow 使用 jlink 观测时需要在 workflow/config.json 里提供 jlink.device"}}
    cmd = [
        PYTHON_EXE,
        str(ROOT_DIR / "jlink" / "scripts" / "jlink_rtt.py"),
        "--workspace",
        str(workspace),
        "--device",
        jlink_cfg["device"],
        "--json",
    ]
    return {"status": "ok", "action": "observe", "summary": "已生成 jlink RTT 观察命令", "details": {"command": cmd}}


def diagnose(workspace: Path, config: dict, discovery: dict, state: dict) -> dict:
    hints = []
    if not discovery["keil"] and not discovery["gcc"]:
        hints.append("当前 workspace 未发现 Keil/GCC 工程")
    if not get_state_entry(state, "last_build"):
        hints.append("尚未生成 last_build，后续 flash/debug 无法自动串联")
    if config.get("preferred_build") == "auto" and discovery["keil"] and discovery["gcc"]:
        hints.append("同时存在 Keil 与 GCC 工程，建议在 workflow/config.json 固定 preferred_build")
    return {
        "status": "ok",
        "action": "diagnose",
        "summary": "workflow 诊断完成",
        "details": {
            "workspace": str(workspace),
            "discovery": discovery,
            "state": {
                "last_build": get_state_entry(state, "last_build"),
                "last_flash": get_state_entry(state, "last_flash"),
                "last_debug": get_state_entry(state, "last_debug"),
                "last_observe": get_state_entry(state, "last_observe"),
            },
            "hints": hints,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="workflow run")
    parser.add_argument("action", choices=["plan", "build", "build-flash", "build-debug", "observe", "diagnose"])
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="workflow config.json 路径")
    parser.add_argument("--build-backend", choices=["auto", "keil", "gcc"], default=None)
    parser.add_argument("--flash-backend", choices=["auto", "jlink", "openocd"], default=None)
    parser.add_argument("--debug-backend", choices=["auto", "jlink", "openocd"], default=None)
    parser.add_argument("--observe-backend", choices=["auto", "jlink", "openocd"], default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(workspace / "workflow" / "config.json"))
    config = load_json_file(config_path)
    state = load_workspace_state(str(workspace))
    discovery = discover_projects(workspace)

    if args.action == "plan":
        cmd = [PYTHON_EXE, str(ROOT_DIR / "workflow" / "scripts" / "workflow_plan.py"), "--workspace", str(workspace), "--json"]
        result = run_json(cmd, workspace)
    elif args.action == "build":
        result = build_project(workspace, config, discovery, args.build_backend)
    elif args.action == "build-flash":
        build_result = build_project(workspace, config, discovery, args.build_backend)
        if build_result.get("status") == "error":
            result = build_result
        else:
            state = load_workspace_state(str(workspace))
            flash_result = flash_project(workspace, config, state, args.flash_backend)
            result = {
                "status": flash_result.get("status", "error"),
                "action": "build-flash",
                "summary": "build-flash 完成" if flash_result.get("status") == "ok" else flash_result.get("error", {}).get("message", "build-flash 失败"),
                "details": {"build": build_result, "flash": flash_result},
            }
    elif args.action == "build-debug":
        build_result = build_project(workspace, config, discovery, args.build_backend)
        if build_result.get("status") == "error":
            result = build_result
        else:
            state = load_workspace_state(str(workspace))
            debug_result = debug_project(workspace, config, state, args.debug_backend)
            result = {
                "status": debug_result.get("status", "error"),
                "action": "build-debug",
                "summary": "build-debug 完成" if debug_result.get("status") == "ok" else debug_result.get("error", {}).get("message", "build-debug 失败"),
                "details": {"build": build_result, "debug": debug_result},
            }
    elif args.action == "observe":
        result = observe_project(workspace, config, args.observe_backend)
    else:
        result = diagnose(workspace, config, discovery, state)

    wrapped = make_result(
        status=result.get("status", "error"),
        action=args.action,
        summary=result.get("summary", "workflow 执行完成"),
        details=result.get("details", {}),
        context=parameter_context(provider="workflow", workspace=str(workspace), config_path=config_path),
        error=result.get("error"),
        timing=make_timing(started_at, (time.time() - started_ts) * 1000),
    )

    if args.as_json:
        output_json(wrapped)
    else:
        print(f"[workflow {args.action}] {wrapped['summary']}")
        if wrapped.get("error"):
            sys.exit(1)


if __name__ == "__main__":
    main()
