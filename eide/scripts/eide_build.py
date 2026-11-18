"""EIDE build / rebuild / clean via unify_builder."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from eide_runtime import (  # noqa: E402
    build_artifacts,
    get_state_entry,
    hidden_subprocess_kwargs,
    is_missing,
    load_json_file,
    load_local_config,
    load_project_config,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path_with_base,
    now_iso,
    output_json,
    parameter_context,
    resolve_param,
    save_project_config,
    update_state_entry,
    workspace_root,
    ArgumentParser,
    run_cli,
)

BUILDER_TIMEOUT_SEC = 1800
ARTIFACT_SUFFIXES = {
    ".axf": "axf_file",
    ".elf": "elf_file",
    ".hex": "hex_file",
    ".bin": "bin_file",
    ".s19": "s19_file",
    ".map": "map_file",
    ".htm": "htm_file",
}


def _find_builder_exe(builder_dir: str) -> str:
    """Locate unify_builder executable within the builder directory."""
    dir_path = Path(builder_dir)
    if not dir_path.is_dir():
        return ""

    candidates = [
        dir_path / "unify_builder.exe",
        dir_path / "unify_builder",
        dir_path / "bin" / "unify_builder.exe",
        dir_path / "bin" / "unify_builder",
    ]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())

    for item in dir_path.iterdir():
        name = item.name.lower()
        if item.is_file() and (
            name == "unify_builder.exe"
            or name == "unify_builder"
            or name.startswith("unify_builder")
        ):
            return str(item.resolve())

    return ""


def _find_builder_params(project_path: Path, config: str) -> Path | None:
    """Find builder.params for the given configuration."""
    build_dir = project_path / "build" / config
    bp = build_dir / "builder.params"
    if bp.is_file():
        return bp

    # Try searching build subdirs
    build_root = project_path / "build"
    if build_root.is_dir():
        for item in build_root.iterdir():
            if item.is_dir():
                bp = item / "builder.params"
                if bp.is_file():
                    return bp

    return None


def _collect_artifacts(project_path: Path, config: str) -> dict[str, str]:
    """Collect build output artifacts."""
    build_dir = project_path / "build" / config
    if not build_dir.is_dir():
        return {}

    details: dict[str, str] = {}
    details["build_dir"] = str(build_dir.resolve())

    # First pass: exact match from builder.params project name
    bp_file = _find_builder_params(project_path, config)
    project_name = ""

    if bp_file:
        try:
            bp_data = json.loads(bp_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            bp_data = {}
        project_name = bp_data.get("name", "") or bp_data.get(
            "ProjectName", ""
        )

    if not project_name:
        for bp_file in build_dir.rglob("builder.params"):
            try:
                bp_data = json.loads(
                    bp_file.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                continue
            n = bp_data.get("name", "") or bp_data.get("ProjectName", "")
            if n:
                project_name = n
                break

    if not project_name:
        eide_yml = project_path / ".eide" / "eide.yml"
        if eide_yml.is_file():
            try:
                import yaml  # type: ignore[import-untyped]
                with open(eide_yml, "r", encoding="utf-8") as f:
                    eide_data = yaml.safe_load(f) or {}
            except Exception:
                eide_data = {}
            project_name = eide_data.get("name", project_path.name)

    # Collect artifacts by name or by suffix
    names_to_try = [project_name] if project_name else []
    names_to_try.append(project_path.name)

    for name in names_to_try:
        for suffix, key in ARTIFACT_SUFFIXES.items():
            candidate = build_dir / f"{name}{suffix}"
            if candidate.is_file() and key not in details:
                details[key] = str(candidate.resolve())

    # Fallback: find by suffix in build dir
    for suffix, key in ARTIFACT_SUFFIXES.items():
        if key not in details:
            matches = sorted(
                build_dir.rglob(f"*{suffix}"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for m in matches:
                if m.is_file():
                    details[key] = str(m.resolve())
                    break

    # Set convenience aliases
    debug_file = details.get("elf_file") or details.get("axf_file")
    flash_file = (
        details.get("hex_file")
        or details.get("bin_file")
        or debug_file
    )
    if debug_file:
        details["debug_file"] = debug_file
    if flash_file:
        details["flash_file"] = flash_file

    return details


def _find_build_log(build_dir: Path) -> str:
    """Find the most relevant build log file."""
    log_candidates = ["compiler.log", "unify_builder.log"]
    for name in log_candidates:
        log_file = build_dir / name
        if log_file.is_file():
            return str(log_file.resolve())
    return ""


def parse_log(log_path: str) -> dict:
    """Parse build log for errors, warnings, and size info."""
    metrics = {"errors": 0, "warnings": 0, "flash_bytes": 0, "ram_bytes": 0}
    if not os.path.isfile(log_path):
        return metrics

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # ARM CC style: "N Error(s), M Warning(s)"
    arm_match = re.search(
        r"(\d+)\s+Error\(s\)\s*,\s*(\d+)\s+Warning\(s\)", content
    )
    if arm_match:
        metrics["errors"] = int(arm_match.group(1))
        metrics["warnings"] = int(arm_match.group(2))

    # GCC style: "error:" count
    if metrics["errors"] == 0:
        err_count = len(
            re.findall(r"(?:^|\s)error:\s", content, re.IGNORECASE)
        )
        warn_count = len(
            re.findall(r"(?:^|\s)warning:\s", content, re.IGNORECASE)
        )
        if err_count > 0 or warn_count > 0:
            metrics["errors"] = err_count
            metrics["warnings"] = warn_count

    # ARM CC Program Size line
    size_match = re.search(
        r"Program Size:\s+Code=(\d+)\s+RO-data=(\d+)\s+"
        r"RW-data=(\d+)\s+ZI-data=(\d+)",
        content,
    )
    if size_match:
        code_size = int(size_match.group(1))
        ro_data = int(size_match.group(2))
        rw_data = int(size_match.group(3))
        zi_data = int(size_match.group(4))
        metrics["flash_bytes"] = code_size + ro_data + rw_data
        metrics["ram_bytes"] = rw_data + zi_data

    # GCC style size info: .text/.data/.bss
    if metrics["flash_bytes"] == 0:
        gcc_size = re.search(
            r"\.text\s+(\d+)\s+.*?\.data\s+(\d+)\s+.*?\.bss\s+(\d+)",
            content,
        )
        if gcc_size:
            text_sz = int(gcc_size.group(1))
            data_sz = int(gcc_size.group(2))
            bss_sz = int(gcc_size.group(3))
            metrics["flash_bytes"] = text_sz + data_sz
            metrics["ram_bytes"] = data_sz + bss_sz

    return metrics


def run_builder(
    builder_exe: str,
    action: str,
    project: str,
    config: str,
    log_dir: str,
    clean_first: bool = False,
) -> dict:
    """Execute unify_builder for build/rebuild/clean."""
    project_path = Path(project).resolve()
    if not project_path.is_dir():
        return {
            "status": "error",
            "action": action,
            "error": {
                "code": "project_not_found",
                "message": f"Project directory not found: {project_path}",
            },
        }

    if not os.path.isfile(builder_exe):
        return {
            "status": "error",
            "action": action,
            "error": {
                "code": "builder_not_found",
                "message": (
                    f"unify_builder not found: {builder_exe}\n"
                    "Please install the EIDE VS Code extension and "
                    "configure builder_dir in config.json"
                ),
            },
        }

    # Find builder.params
    bp_file = _find_builder_params(project_path, config)
    if not bp_file:
        return {
            "status": "error",
            "action": action,
            "error": {
                "code": "params_not_found",
                "message": (
                    f"builder.params not found for config '{config}'\n"
                    "Please ensure the project has been opened with "
                    "EIDE at least once to generate builder.params"
                ),
            },
        }

    build_dir = bp_file.parent
    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)

    project_name = project_path.name
    log_file = (
        log_path / f"{project_name}-{config}-{action}.log"
    )

    # unify_builder commands:
    #   build   - incremental build
    #   rebuild - full rebuild
    #   clean   - clean build outputs
    action_map = {
        "build": "build",
        "rebuild": "rebuild",
        "clean": "clean",
    }
    builder_action = action_map.get(action, action)

    cmd = [builder_exe, builder_action, "--params-file", str(bp_file)]

    if clean_first and action == "rebuild":
        cmd = [builder_exe, "clean", "--params-file", str(bp_file)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=BUILDER_TIMEOUT_SEC,
            cwd=str(project_path),
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "action": action,
            "error": {
                "code": "timeout",
                "message": f"Build timed out ({BUILDER_TIMEOUT_SEC}s)",
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "exec_error", "message": str(exc)},
        }

    # Write log
    log_content_parts = []
    if proc.stdout:
        log_content_parts.append(proc.stdout)
    if proc.stderr:
        log_content_parts.append("--- STDERR ---")
        log_content_parts.append(proc.stderr)
    build_log = _find_build_log(build_dir)
    if build_log and os.path.isfile(build_log):
        log_content_parts.append(f"--- BUILD LOG: {build_log} ---")
        try:
            with open(
                build_log, "r", encoding="utf-8", errors="replace"
            ) as f:
                log_content_parts.append(f.read())
        except OSError:
            pass
    log_file.write_text(
        "\n".join(log_content_parts), encoding="utf-8"
    )

    # If clean_first and action is rebuild, now run the actual build
    if clean_first and action == "rebuild":
        cmd = [builder_exe, "build", "--params-file", str(bp_file)]
        try:
            proc2 = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=BUILDER_TIMEOUT_SEC,
                cwd=str(project_path),
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "action": action,
                "error": {
                    "code": "timeout",
                    "message": f"Build timed out ({BUILDER_TIMEOUT_SEC}s)",
                },
            }
        except Exception as exc:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "exec_error", "message": str(exc)},
            }
        proc = proc2

        build_log = _find_build_log(build_dir)
        if build_log and os.path.isfile(build_log):
            try:
                with open(build_log, "r", encoding="utf-8", errors="replace") as f:
                    log_file.write_text(
                        log_file.read_text(encoding="utf-8")
                        + f"\n--- AFTER CLEAN ---\n{f.read()}",
                        encoding="utf-8",
                    )
            except OSError:
                pass

    # Parse results
    primary_log = build_log if build_log else str(log_file)
    metrics = parse_log(primary_log)

    rc = proc.returncode
    is_error = rc != 0 or metrics["errors"] > 0
    status = "error" if is_error else "ok"

    details: dict[str, object] = {
        "project": str(project_path),
        "config": config,
        "build_dir": str(build_dir),
        "log_file": str(log_file.resolve()),
        "builder_exit_code": rc,
        **_collect_artifacts(project_path, config),
    }

    result: dict[str, object] = {
        "status": status,
        "action": action,
        "metrics": metrics,
        "details": details,
    }

    if status == "error":
        error_msg = f"Build failed with exit code {rc}"
        if proc.stderr:
            stderr_lines = [
                l for l in proc.stderr.splitlines() if l.strip()
            ]
            if stderr_lines:
                first_error = stderr_lines[0][:200]
                error_msg = (
                    f"Build failed: {first_error}"
                )
        result["error"] = {
            "code": "build_failed",
            "message": error_msg,
        }

    return result


def _build_summary(
    action: str, status: str, metrics: dict
) -> str:
    errors = metrics.get("errors", 0)
    warnings = metrics.get("warnings", 0)
    if status == "error":
        return f"{action} failed, errors={errors} warnings={warnings}"
    if action in ("build", "rebuild"):
        return f"{action} succeeded, errors={errors} warnings={warnings}"
    return f"{action} succeeded"


def _next_actions(
    action: str, artifacts: dict
) -> list[str]:
    actions: list[str] = []
    if action in ("build", "rebuild") and artifacts.get("flash_file"):
        actions.append(
            "artifacts.flash_file can be reused for flash via jlink/openocd"
        )
    if action in ("build", "rebuild") and artifacts.get("debug_file"):
        actions.append(
            "artifacts.debug_file can be reused for gdb debugging"
        )
    return actions


def _make_relative_to_workspace(
    workspace: Path, path: str
) -> str:
    try:
        p = Path(path).resolve()
        rel = p.relative_to(workspace.resolve())
        return str(rel).replace("\\", "/")
    except ValueError:
        return path


def main() -> None:
    parser = ArgumentParser(
        description="EIDE build / rebuild / clean"
    )
    parser.add_argument(
        "action", choices=["build", "rebuild", "clean"]
    )
    parser.add_argument(
        "--builder-dir", default=None, help="unify_builder directory path"
    )
    parser.add_argument("--builder-exe", help="unify_builder executable")
    parser.add_argument(
        "--project", default=None, help="EIDE project root directory"
    )
    parser.add_argument(
        "--config", default=None, help="Build configuration name"
    )
    parser.add_argument(
        "--log-dir", default=None, help="Log output directory"
    )
    parser.add_argument(
        "--clean-first", action="store_true",
        help="Clean before rebuild"
    )
    parser.add_argument(
        "--config-file", default=None, help="Path to skill config.json"
    )
    parser.add_argument(
        "--workspace", default=None,
        help="Workspace root directory, default cwd"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json"
    )
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)

    local_config = load_json_file(args.config_file) if args.config_file else load_local_config(__file__)
    project_config = load_project_config(str(workspace))
    state = load_workspace_state(str(workspace))
    last_build = get_state_entry(state, "last_build")

    parameter_sources: dict[str, str] = {}

    try:
        builder_name = args.builder_exe or local_config.get("builder_exe") or "unify_builder"
        builder_dir = args.builder_dir or local_config.get("builder_dir")
        if args.builder_exe:
            builder_exe = shutil.which(builder_name)
            parameter_sources["builder_exe"] = "cli"
        elif builder_dir:
            candidate = Path(builder_dir).expanduser() / builder_name
            builder_exe = str(candidate) if candidate.is_file() else _find_builder_exe(builder_dir)
            parameter_sources["builder_exe"] = "cli:builder_dir" if args.builder_dir else "config:builder_dir"
        else:
            builder_exe = shutil.which(builder_name)
            parameter_sources["builder_exe"] = "config:builder_exe" if local_config.get("builder_exe") else f"path:{builder_name}"
        if not builder_exe:
            raise ValueError(f"unify_builder executable not found: {builder_name}")

        # project: CLI > project_config > state > required
        project, parameter_sources["project"] = resolve_param(
            "project",
            args.project,
            normalize_as_path=True,
            workspace=str(workspace),
        )
        if is_missing(project) and not is_missing(
            project_config.get("project")
        ):
            project = normalize_path_with_base(
                project_config.get("project"), workspace
            )
            parameter_sources["project"] = "project_config:project"
        if is_missing(project) and not is_missing(
            last_build.get("project")
        ):
            project = normalize_path_with_base(
                str(last_build.get("project")), workspace
            )
            parameter_sources["project"] = "state:project"
        if is_missing(project):
            raise ValueError("missing required param: project")

        # config: CLI > project_config > state
        config, parameter_sources["config"] = resolve_param(
            "config",
            args.config,
        )
        if is_missing(config) and not is_missing(
            project_config.get("config")
        ):
            config = project_config.get("config")
            parameter_sources["config"] = "project_config:config"
        if is_missing(config) and not is_missing(
            last_build.get("config")
        ):
            config = last_build.get("config")
            parameter_sources["config"] = "state:config"

        # log_dir: CLI > project_config > local_config > default
        log_dir_raw = (
            args.log_dir
            or project_config.get("log_dir")
            or local_config.get("log_dir")
        )
        log_dir = normalize_path_with_base(
            log_dir_raw or ".embeddedskills/build", workspace
        )
        if args.log_dir:
            parameter_sources["log_dir"] = "cli"
        elif project_config.get("log_dir"):
            parameter_sources["log_dir"] = "project_config:log_dir"
        elif local_config.get("log_dir"):
            parameter_sources["log_dir"] = "config:log_dir"
        else:
            parameter_sources["log_dir"] = "default"

    except ValueError as exc:
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            details={},
            context=parameter_context(
                provider="eide",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            error={"code": "missing_param", "message": str(exc)},
            timing=make_timing(
                started_at, (time.time() - started_ts) * 1000
            ),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    raw_result = run_builder(
        builder_exe=builder_exe,
        action=args.action,
        project=project,
        config=config or "",
        log_dir=log_dir,
        clean_first=args.clean_first,
    )
    elapsed_ms = (time.time() - started_ts) * 1000

    if raw_result["status"] == "error":
        result = make_result(
            status="error",
            action=args.action,
            summary=raw_result["error"]["message"],
            details=raw_result.get("details", {}),
            context=parameter_context(
                provider="eide",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            error=raw_result["error"],
            timing=make_timing(started_at, elapsed_ms),
        )
    else:
        details = raw_result["details"]
        artifacts = build_artifacts(
            elf_file=details.get("elf_file"),
            hex_file=details.get("hex_file"),
            bin_file=details.get("bin_file"),
            axf_file=details.get("axf_file"),
            flash_file=details.get("flash_file"),
            debug_file=details.get("debug_file"),
            build_dir=details.get("build_dir"),
            log_file=details.get("log_file"),
        )
        summary = _build_summary(
            args.action, raw_result["status"], raw_result["metrics"]
        )
        state_info = None
        if raw_result["status"] == "ok":
            state_info = update_state_entry(
                "last_build",
                {
                    "provider": "eide",
                    "action": args.action,
                    "project": project,
                    "config": config,
                    "log_dir": log_dir,
                    "artifacts": artifacts,
                    **artifacts,
                },
                str(workspace),
            )

            # Write back confirmed parameters to project config
            project_rel = _make_relative_to_workspace(
                workspace, project
            )
            save_project_config(
                str(workspace),
                {
                    "project": project_rel,
                    "config": config or "",
                    "log_dir": _make_relative_to_workspace(
                        workspace, log_dir
                    ),
                },
            )

        result = make_result(
            status=raw_result["status"],
            action=args.action,
            summary=summary,
            details=details,
            context=parameter_context(
                provider="eide",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
            ),
            artifacts=artifacts,
            metrics=raw_result["metrics"],
            state=state_info,
            next_actions=_next_actions(args.action, artifacts),
            timing=make_timing(started_at, elapsed_ms),
        )

    if args.as_json:
        output_json(result)
        if result["status"] == "error":
            sys.exit(1)
        return

    if result["status"] == "ok":
        print(f"[{args.action}] {result['summary']}")
        if result.get("artifacts", {}).get("log_file"):
            print(f"  Log: {result['artifacts']['log_file']}")
        if result.get("artifacts", {}).get("flash_file"):
            print(f"  Flash: {result['artifacts']['flash_file']}")
        if result.get("artifacts", {}).get("debug_file"):
            print(f"  Debug: {result['artifacts']['debug_file']}")
        if result.get("metrics", {}).get("flash_bytes"):
            m = result["metrics"]
            print(
                f"  Size: Flash={m['flash_bytes']}B "
                f"RAM={m.get('ram_bytes', 0)}B"
            )
    else:
        error = result.get("error", {})
        print(
            f"[{args.action}] Failed — "
            f"{error.get('message', result['summary'])}",
            file=sys.stderr,
        )
        if result.get("details", {}).get("log_file"):
            print(
                f"  Log: {result['details']['log_file']}",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    run_cli(main)
