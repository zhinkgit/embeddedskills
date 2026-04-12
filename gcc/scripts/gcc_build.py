"""GCC 嵌入式工程构建：configure / build / rebuild / clean"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_build_dir(project: Path, preset: str, presets_file: Path) -> Path:
    """从 CMakePresets.json 解析 preset 对应的 binaryDir"""
    if presets_file.exists():
        data = json.loads(presets_file.read_text(encoding="utf-8"))
        all_presets = {p["name"]: p for p in data.get("configurePresets", [])}
        p = all_presets.get(preset, {})
        binary_dir = p.get("binaryDir", "")
        if not binary_dir:
            # 检查 inherits
            inherits = p.get("inherits")
            if inherits and isinstance(inherits, str):
                parent = all_presets.get(inherits, {})
                binary_dir = parent.get("binaryDir", "")
        if binary_dir:
            binary_dir = binary_dir.replace("${sourceDir}", str(project))
            binary_dir = binary_dir.replace("${presetName}", preset)
            return Path(binary_dir)
    # 默认
    return project / "build" / preset


def _find_elf(build_dir: Path, project_name: str) -> str:
    """在构建目录中查找 .elf 文件，优先匹配项目名"""
    elfs = list(build_dir.glob("*.elf"))
    if not elfs:
        elfs = list(build_dir.rglob("*.elf"))
    if not elfs:
        return ""
    # 优先匹配项目名
    for e in elfs:
        if e.stem.lower() == project_name.lower():
            return str(e)
    return str(elfs[0])


def _parse_build_output(output: str) -> dict:
    """从 GCC/Ninja 构建输出中提取错误/警告数量和内存使用"""
    summary = {"errors": 0, "warnings": 0, "flash_bytes": 0, "ram_bytes": 0}

    # GCC 错误格式: path/file.c:123:45: error: message
    errors = re.findall(r":\d+:\d+:\s+error:", output)
    warnings = re.findall(r":\d+:\d+:\s+warning:", output)
    summary["errors"] = len(errors)
    summary["warnings"] = len(warnings)

    # 链接器 --print-memory-usage 输出
    # 格式：FLASH:  99328 B   1 MB   9.47%
    for m in re.finditer(
        r"(FLASH|RAM|CCMRAM)\s*:\s*([\d]+)\s*(B|KB|MB)",
        output, re.IGNORECASE
    ):
        region = m.group(1).upper()
        value = int(m.group(2))
        unit = m.group(3).upper()
        if unit == "KB":
            value *= 1024
        elif unit == "MB":
            value *= 1024 * 1024
        if region == "FLASH":
            summary["flash_bytes"] = value
        elif region == "RAM":
            summary["ram_bytes"] = value

    return summary


def _extract_first_error(output: str) -> str:
    """提取第一个 GCC 错误行"""
    for line in output.splitlines():
        if re.search(r":\d+:\d+:\s+error:", line):
            # 去掉 Ninja 进度前缀 [x/y]
            line = re.sub(r"^\[\d+/\d+\]\s*", "", line)
            return line.strip()
    return ""


def run_configure(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    """执行 cmake --preset"""
    project_path = Path(project).resolve()
    if not (project_path / "CMakeLists.txt").exists():
        return _error("configure", "project_not_found",
                       f"CMakeLists.txt 不存在: {project_path}")

    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{project_path.name}-{preset}-configure.log"

    cmd = [cmake_exe, "--preset", preset]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=str(project_path), encoding="utf-8", errors="replace",
        )
        output = proc.stdout + "\n" + proc.stderr
        log_file.write_text(output, encoding="utf-8")

        if proc.returncode != 0:
            return {
                "status": "error",
                "action": "configure",
                "error": {"code": "configure_failed", "message": output.strip()[-500:]},
                "details": {"project": str(project_path), "preset": preset,
                            "log_file": str(log_file)},
            }

        return {
            "status": "ok",
            "action": "configure",
            "summary": "配置完成",
            "details": {"project": str(project_path), "preset": preset,
                        "log_file": str(log_file)},
        }
    except subprocess.TimeoutExpired:
        return _error("configure", "timeout", "cmake 配置超时(300s)")
    except FileNotFoundError:
        return _error("configure", "cmake_not_found", f"cmake 不存在: {cmake_exe}")


def run_build(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    """执行 cmake --build"""
    project_path = Path(project).resolve()
    presets_file = project_path / "CMakePresets.json"
    build_dir = _resolve_build_dir(project_path, preset, presets_file)

    # 检查是否已 configure
    has_ninja = (build_dir / "build.ninja").exists()
    has_makefile = (build_dir / "Makefile").exists()
    if not has_ninja and not has_makefile:
        return _error("build", "not_configured",
                       f"构建目录不存在或未配置: {build_dir}，请先执行 configure")

    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{project_path.name}-{preset}-build.log"

    cmd = [cmake_exe, "--build", str(build_dir)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            cwd=str(project_path), encoding="utf-8", errors="replace",
        )
        output = proc.stdout + "\n" + proc.stderr
        log_file.write_text(output, encoding="utf-8")

        summary = _parse_build_output(output)

        if proc.returncode != 0 or summary["errors"] > 0:
            first_err = _extract_first_error(output)
            return {
                "status": "error",
                "action": "build",
                "summary": summary,
                "error": {"code": "build_failed",
                          "message": first_err or f"构建失败，返回码: {proc.returncode}"},
                "details": {
                    "project": str(project_path), "preset": preset,
                    "build_dir": str(build_dir), "log_file": str(log_file),
                },
            }

        elf_file = _find_elf(build_dir, project_path.name)

        return {
            "status": "ok",
            "action": "build",
            "summary": summary,
            "details": {
                "project": str(project_path), "preset": preset,
                "build_dir": str(build_dir), "elf_file": elf_file,
                "log_file": str(log_file),
            },
        }
    except subprocess.TimeoutExpired:
        return _error("build", "timeout", "构建超时(600s)")
    except FileNotFoundError:
        return _error("build", "cmake_not_found", f"cmake 不存在: {cmake_exe}")


def run_clean(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    """清理构建目录"""
    project_path = Path(project).resolve()
    presets_file = project_path / "CMakePresets.json"
    build_dir = _resolve_build_dir(project_path, preset, presets_file)

    if not build_dir.exists():
        return {
            "status": "ok",
            "action": "clean",
            "summary": "构建目录不存在，无需清理",
            "details": {"project": str(project_path), "preset": preset,
                        "build_dir": str(build_dir)},
        }

    # 优先用 cmake --build --target clean
    has_build_system = (build_dir / "build.ninja").exists() or \
                       (build_dir / "Makefile").exists()
    if has_build_system:
        try:
            proc = subprocess.run(
                [cmake_exe, "--build", str(build_dir), "--target", "clean"],
                capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0:
                return {
                    "status": "ok",
                    "action": "clean",
                    "summary": "清理完成（cmake --target clean）",
                    "details": {"project": str(project_path), "preset": preset,
                                "build_dir": str(build_dir)},
                }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 回退：删除整个构建目录
    try:
        shutil.rmtree(str(build_dir))
        return {
            "status": "ok",
            "action": "clean",
            "summary": f"已删除构建目录: {build_dir}",
            "details": {"project": str(project_path), "preset": preset,
                        "build_dir": str(build_dir)},
        }
    except OSError as e:
        return _error("clean", "clean_failed", f"删除构建目录失败: {e}")


def run_rebuild(cmake_exe: str, project: str, preset: str, log_dir: str) -> dict:
    """clean + configure + build"""
    # clean
    result = run_clean(cmake_exe, project, preset, log_dir)
    if result["status"] == "error":
        result["action"] = "rebuild"
        return result

    # configure
    result = run_configure(cmake_exe, project, preset, log_dir)
    if result["status"] == "error":
        result["action"] = "rebuild"
        return result

    # build
    result = run_build(cmake_exe, project, preset, log_dir)
    result["action"] = "rebuild"
    return result


def _error(action: str, code: str, message: str) -> dict:
    return {
        "status": "error",
        "action": action,
        "error": {"code": code, "message": message},
    }


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="GCC 嵌入式工程构建")
    parser.add_argument("action", choices=["configure", "build", "rebuild", "clean"])
    parser.add_argument("--cmake", default="cmake", help="cmake 可执行文件路径")
    parser.add_argument("--project", required=True, help="工程根目录")
    parser.add_argument("--preset", required=True, help="CMake preset 名称")
    parser.add_argument("--log-dir", default=".build", help="日志输出目录")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    actions = {
        "configure": run_configure,
        "build": run_build,
        "rebuild": run_rebuild,
        "clean": run_clean,
    }

    result = actions[args.action](
        cmake_exe=args.cmake,
        project=args.project,
        preset=args.preset,
        log_dir=args.log_dir,
    )

    if args.as_json:
        output_json(result)
    else:
        if result["status"] == "ok":
            s = result.get("summary", "")
            if isinstance(s, dict):
                print(f"[{args.action}] 成功 — errors: {s.get('errors',0)}, warnings: {s.get('warnings',0)}")
                if s.get("flash_bytes"):
                    print(f"  Flash: {s['flash_bytes']} bytes, RAM: {s['ram_bytes']} bytes")
            else:
                print(f"[{args.action}] {s}")
            details = result.get("details", {})
            if "log_file" in details:
                print(f"  日志: {details['log_file']}")
            if "elf_file" in details and details["elf_file"]:
                print(f"  ELF: {details['elf_file']}")
        else:
            err = result.get("error", {})
            print(f"[{args.action}] 失败 — {err.get('message', '未知错误')}", file=sys.stderr)
            details = result.get("details", {})
            if "log_file" in details:
                print(f"  日志: {details['log_file']}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
