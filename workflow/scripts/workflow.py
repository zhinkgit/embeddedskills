from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


BUILD_BACKENDS = ("gcc", "keil", "eide")
PROBE_BACKENDS = ("jlink", "openocd", "probe-rs")
STAGES = ("build", "flash", "debug", "observe")
ARTIFACT_SUFFIXES = {".elf", ".axf", ".hex", ".bin"}
SKIP_DIRECTORIES = {".git", ".venv", ".embeddedskills", "node_modules", "__pycache__"}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 应为 JSON 对象")
    return value


def section(mapping: dict, key: str) -> dict:
    value = mapping.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} 应为配置对象")
    return value


def discover(workspace: Path) -> tuple[dict, list[Path]]:
    projects = {backend: set() for backend in BUILD_BACKENDS}
    artifacts = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRECTORIES]
        directory = Path(root)
        for name in files:
            path = directory / name
            suffix = path.suffix.lower()
            if suffix in (".uvproj", ".uvprojx"):
                projects["keil"].add(path)
            elif name == "CMakeLists.txt":
                projects["gcc"].add(directory)
            elif name == "eide.yml" and directory.name == ".eide":
                projects["eide"].add(directory.parent)
            elif suffix in ARTIFACT_SUFFIXES:
                artifacts.append(path)
    return {backend: sorted(paths) for backend, paths in projects.items()}, sorted(artifacts)


def choose(explicit, configured, candidates):
    desired = explicit or configured
    if desired and desired != "auto":
        return desired
    return candidates[0] if len(candidates) == 1 else None


def under(root: Path, value) -> Path:
    return (root / str(value)).resolve()


class Step:
    def __init__(self, stage: str, cwd: Path, backend: str | None, candidates):
        self.cwd = cwd
        self.data = {"stage": stage, "backend": backend, "status": "blocked",
                     "candidates": candidates, "requires": [], "commands": []}

    def need(self, message: str):
        self.data["requires"].append(message)

    def tool(self, tools: dict, key: str, *defaults: str) -> str | None:
        override = tools.get(key)
        for name in (str(override),) if override else defaults:
            found = shutil.which(name)
            if found:
                return found
        self.need(f"需要可执行程序：{override or ' / '.join(defaults)}（workflow.tools.{key}）")
        return None

    def file(self, path: Path, label: str) -> bool:
        if path.is_file():
            return True
        self.need(f"缺少{label}：{path}")
        return False

    def command(self, tool: str | None, *args, cwd: Path | None = None, background=False):
        if tool and not self.data["requires"]:
            self.data["commands"].append({"argv": [tool, *map(str, args)],
                                          "cwd": str(cwd or self.cwd), "background": background})

    def payload(self) -> dict:
        if self.data["commands"] and not self.data["requires"]:
            self.data["status"] = "ready"
        elif self.data["requires"]:
            self.data["commands"] = []
        return self.data


def plan_cmake(step, args, params, tools, project):
    preset = args.build_profile or params.get("preset")
    manifests = [read_object(project / name) for name in ("CMakePresets.json", "CMakeUserPresets.json")]
    build_presets = {entry["name"]: entry for manifest in manifests for entry in manifest.get("buildPresets", [])
                     if not entry.get("hidden")}
    configure_presets = {entry["name"] for manifest in manifests for entry in manifest.get("configurePresets", [])
                         if not entry.get("hidden")}
    if not preset:
        choices = sorted(set(build_presets) | configure_presets)
        preset = choices[0] if len(choices) == 1 else None
        step.data["profile_candidates"] = choices
    exe = step.tool(tools, "cmake", "cmake")
    if preset in build_presets:
        configure = build_presets[preset].get("configurePreset")
        if configure:
            step.command(exe, "--preset", configure, cwd=project)
        step.command(exe, "--build", "--preset", preset, cwd=project)
    elif preset in configure_presets and params.get("build_dir"):
        step.command(exe, "--preset", preset, cwd=project)
        step.command(exe, "--build", under(project, params["build_dir"]), cwd=project)
    else:
        step.need("指定 CMake Build Preset，或提供 Configure Preset 和对应的 workflow.gcc.build_dir")


def plan_build(args, config, tools, projects) -> dict:
    available = [name for name, paths in projects.items() if paths or section(config, name).get("project")]
    if args.project:
        project = under(args.workspace, args.project)
        inferred = []
        if project.suffix.lower() in {".uvproj", ".uvprojx"}:
            inferred.append("keil")
        if (project / "CMakeLists.txt").is_file():
            inferred.append("gcc")
        if (project / ".eide" / "eide.yml").is_file():
            inferred.append("eide")
        available = inferred or available
    selected = choose(args.build_backend, config.get("build_backend"), available)
    candidates = {name: list(map(str, paths)) for name, paths in projects.items() if paths}
    step = Step("build", args.workspace, selected, candidates)
    if not selected:
        step.need("指定 --build-backend；未发现工程时请提供 --project")
        return step.payload()
    if selected not in BUILD_BACKENDS:
        raise ValueError(f"未知构建后端：{selected}")
    params = section(config, selected)
    paths = projects[selected]
    value = args.project or params.get("project")
    project = under(args.workspace, value) if value else (paths[0] if len(paths) == 1 else None)
    if project is None:
        step.need("指定唯一构建工程 --project")
        return step.payload()
    if selected == "keil":
        if step.file(project, "Keil 工程"):
            target = args.build_profile or params.get("target")
            if not target:
                targets = [node.text for node in ET.parse(project).findall(".//TargetName") if node.text]
                if len(targets) != 1:
                    step.data["profile_candidates"] = targets
                    step.need("指定唯一 Keil Target（--build-profile 或 workflow.keil.target）")
                else:
                    target = targets[0]
            argv = ["-b", project, "-j0"]
            if target:
                argv.extend(["-t", target])
            step.command(step.tool(tools, "uv4", "UV4.exe", "UV4"), *argv, cwd=project.parent)
    elif selected == "gcc":
        if step.file(project / "CMakeLists.txt", "CMake 工程"):
            plan_cmake(step, args, params, tools, project)
    elif step.file(project / ".eide" / "eide.yml", "EIDE 工程"):
        profile = args.build_profile or params.get("config")
        configured = params.get("params_file")
        paths = sorted((project / "build").rglob("builder.params"))
        if profile:
            paths = [path for path in paths if path.parent.name.casefold() == str(profile).casefold()]
        builder_params = under(project, configured) if configured else (paths[0] if len(paths) == 1 else None)
        if not builder_params:
            step.need("在 EIDE 生成 builder.params 并指定 workflow.eide.params_file 或唯一构建配置")
            step.data["parameters_candidates"] = list(map(str, paths))
        elif step.file(builder_params, "EIDE builder.params"):
            step.command(step.tool(tools, "unify_builder", "unify_builder", "unify_builder.exe"),
                         "build", "--params-file", builder_params, cwd=project)
    return step.payload()


def artifact_for(step: Step, explicit, configured, discovered, suffixes) -> Path | None:
    value = explicit or configured
    paths = [path for path in discovered if path.suffix.lower() in suffixes]
    path = under(step.cwd, value) if value else (paths[0] if len(paths) == 1 else None)
    if path is None:
        step.data["artifact_candidates"] = list(map(str, paths))
        step.need("提供此步骤的唯一产物路径")
    elif path.suffix.lower() not in suffixes:
        step.need(f"不支持的产物类型：{path.suffix}")
        return None
    elif not step.file(path, "产物"):
        return None
    return path


def openocd_options(step, params) -> list:
    items = [params["board"]] if params.get("board") else [params.get("interface"), params.get("target")]
    if any(not item for item in items):
        step.need("指定 OpenOCD board，或 interface 和 target")
        return []
    search = ["-s", under(step.cwd, params["search"])] if params.get("search") else []
    return [*search, *(value for item in items for value in ("-f", item))]


def probe_options(step, params) -> list:
    if not params.get("chip"):
        step.need("指定 workflow.probe-rs.chip")
    options = ["--non-interactive", "--chip", params.get("chip")]
    for key in ("protocol", "speed", "probe"):
        if params.get(key):
            options.extend([f"--{key}", params[key]])
    if params.get("connect_under_reset"):
        options.append("--connect-under-reset")
    return options


def jlink_options(step, params) -> list:
    if not params.get("device"):
        step.need("指定 workflow.jlink.device")
    return ["-device", params.get("device"), "-if", params.get("interface", "SWD"),
            "-speed", params.get("speed", 4000)]


def flash(step, args, config, params, tools, artifacts):
    selected = step.data["backend"]
    if selected == "jlink":
        script = params.get("command_file")
        if not script:
            step.need("提供 workflow.jlink.command_file（含目标连接及烧录指令的 J-Link 命令文件）")
        elif step.file(under(step.cwd, script), "J-Link 命令文件"):
            step.command(step.tool(tools, "jlink", "JLink.exe", "JLinkExe"),
                         "-CommandFile", under(step.cwd, script))
        return
    path = artifact_for(step, args.firmware, config.get("firmware"), artifacts, ARTIFACT_SUFFIXES)
    if path is None:
        return
    address = params.get("address")
    if path.suffix.lower() == ".bin":
        try:
            if isinstance(address, bool):
                raise ValueError
            address = hex(int(str(address), 0))
        except (TypeError, ValueError):
            step.need("BIN 烧录需要合法的 address")
    if selected == "openocd":
        if any(c in path.as_posix() for c in "{}\n\r"):
            step.need("OpenOCD 产物路径不能含花括号或换行")
        extra = f" {address}" if path.suffix.lower() == ".bin" else ""
        step.command(step.tool(tools, "openocd", "openocd"), *openocd_options(step, params),
                     "-c", f"program {{{path.as_posix()}}} verify reset exit{extra}")
        return
    fmt = {".elf": "elf", ".axf": "elf", ".hex": "hex", ".bin": "bin"}[path.suffix.lower()]
    base = ["--base-address", address] if fmt == "bin" else []
    step.command(step.tool(tools, "probe_rs", "probe-rs"), "download", *probe_options(step, params),
                 "--binary-format", fmt, *base, path)


def debug(step, path, params, tools):
    selected = step.data["backend"]
    port = int(params.get("gdb_port", 3333 if selected == "openocd" else 2331))
    if not 1 <= port <= 65535:
        raise ValueError("gdb_port 应在 1–65535 范围内")
    if selected == "jlink":
        step.command(step.tool(tools, "jlink_gdb", "JLinkGDBServerCL.exe", "JLinkGDBServerCLExe"),
                     *jlink_options(step, params), "-port", port, "-LocalhostOnly", "-singlerun", background=True)
    elif selected == "openocd":
        step.command(step.tool(tools, "openocd", "openocd"), *openocd_options(step, params),
                     "-c", f"gdb_port {port}", background=True)
    else:
        step.command(step.tool(tools, "probe_rs", "probe-rs"), "gdb", *probe_options(step, params),
                     "--gdb-connection-string", f"localhost:{port}", background=True)
    step.command(step.tool(tools, "gdb", "arm-none-eabi-gdb"), path, "-ex", f"target extended-remote localhost:{port}")


def observe(step, args, config, params, tools, artifacts):
    selected = step.data["backend"]
    if selected == "probe-rs":
        path = artifact_for(step, args.elf, config.get("elf"), artifacts, {".elf", ".axf"})
        step.command(step.tool(tools, "probe_rs", "probe-rs"), "attach", *probe_options(step, params), path)
    elif selected == "jlink":
        command = params.get("observe_argv")
        if not isinstance(command, list) or not command or not all(isinstance(word, str) and word for word in command):
            step.need("指定 workflow.jlink.observe_argv，包含 RTT/SWO 客户端程序和连接参数")
        else:
            step.command(step.tool({}, "observe_argv", command[0]), *command[1:])
    else:
        step.command(step.tool(tools, "openocd", "openocd"), *openocd_options(step, params),
                     "-c", "init; reset halt; arm semihosting enable; resume")


def verify_script() -> Path:
    return Path(__file__).resolve().parents[2] / "verify" / "scripts" / "verify.py"


def plan_verify(args, workspace: Path, params: dict) -> dict:
    step = Step("verify", workspace, "verify", [])
    action = args.verify_action or params.get("action") or "run"
    if action not in ("run", "unit", "smoke"):
        step.need("verify.action 必须为 run、unit 或 smoke")
        return step.payload()
    script = verify_script()
    if not step.file(script, "verify 脚本"):
        return step.payload()
    value = args.verify_plan or params.get("plan")
    plan_path = under(workspace, value) if value else None
    if plan_path is None:
        for candidate in (".embeddedskills/verify/plan.json", ".embeddedskills/verify.json", "verify.plan.json"):
            path = workspace / candidate
            if path.is_file():
                plan_path = path
                break
    if plan_path is None:
        step.need("提供 verify 计划（--verify-plan 或 .embeddedskills/config.json 的 verify.plan）")
        return step.payload()
    if not step.file(plan_path, "verify 计划"):
        return step.payload()
    step.data["action"] = action
    step.data["plan"] = str(plan_path)
    step.command(sys.executable, script, action, "--workspace", workspace, "--json", "--plan", plan_path,
                 cwd=workspace)
    return step.payload()


def plan_probe(stage, args, config, tools, artifacts) -> dict:
    available = [name for name in PROBE_BACKENDS if section(config, name)]
    selected = choose(getattr(args, f"{stage}_backend"), config.get(f"{stage}_backend"), available)
    step = Step(stage, args.workspace, selected, available or list(PROBE_BACKENDS))
    if not selected:
        step.need(f"指定 --{stage}-backend 或配置唯一的硬件后端")
        return step.payload()
    if selected not in PROBE_BACKENDS:
        raise ValueError(f"未知硬件后端：{selected}")
    params = section(config, selected)
    if stage == "flash":
        flash(step, args, config, params, tools, artifacts)
    elif stage == "observe":
        observe(step, args, config, params, tools, artifacts)
    else:
        path = artifact_for(step, args.elf, config.get("elf"), artifacts, {".elf", ".axf"})
        if path:
            debug(step, path, params, tools)
    return step.payload()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    as_json = "--json" in argv
    action = "workflow"
    parser = Parser(description="只读工程规划与诊断")
    parser.add_argument("action", choices=("plan", "diagnose"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--project", type=Path)
    parser.add_argument("--build-profile", help="Keil Target、GCC Build Preset 或 EIDE 配置")
    parser.add_argument("--firmware", type=Path)
    parser.add_argument("--elf", type=Path)
    parser.add_argument("--build-backend", choices=(*BUILD_BACKENDS, "auto"))
    for stage in STAGES[1:]:
        parser.add_argument(f"--{stage}-backend", choices=(*PROBE_BACKENDS, "auto"))
    parser.add_argument("--verify-action", choices=("run", "unit", "smoke"))
    parser.add_argument("--verify-plan", type=Path)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        action = args.action
        args.workspace = args.workspace.resolve(strict=True)
        if not args.workspace.is_dir():
            raise ValueError("工作区应为目录")
        raw_config = read_object(args.workspace / ".embeddedskills" / "config.json")
        config = section(raw_config, "workflow")
        verify_config = section(raw_config, "verify")
        tools = section(read_object(Path(__file__).resolve().parents[1] / "config.json"), "tools")
        projects, artifacts = discover(args.workspace)
        steps = [plan_build(args, config, tools, projects)]
        steps.extend(plan_probe(stage, args, config, tools, artifacts) for stage in STAGES[1:])
        steps.append(plan_verify(args, args.workspace, verify_config))
        details = {"workspace": str(args.workspace),
                   "projects": {name: list(map(str, paths)) for name, paths in projects.items()},
                   "artifacts": list(map(str, artifacts))}
        if action == "plan":
            details["steps"] = steps
        else:
            details["issues"] = [
                {"stage": step["stage"], "backend": step["backend"], "suggestions": step["requires"],
                 "candidates": step["candidates"]}
                for step in steps if step["requires"]
            ]
        payload = {"status": "ok", "action": action, "summary": "只读检查完成", "details": details}
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError) as exc:
        payload = {"status": "error", "action": action, "summary": str(exc), "details": {},
                   "error": {"code": "invalid_workflow_input", "message": str(exc)}}
    if as_json:
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
