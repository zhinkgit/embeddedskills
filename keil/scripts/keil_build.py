"""Keil MDK 构建 / 重建 / 清理 / 烧录"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# UV4.exe ERRORLEVEL 映射
ERRORLEVEL_MAP = {
    0: ("ok", "无错误或警告"),
    1: ("ok", "有警告"),
    2: ("error", "有错误"),
    3: ("error", "致命错误"),
    11: ("error", "无法打开工程文件"),
    12: ("error", "设备数据库缺失"),
    13: ("error", "写入错误"),
    15: ("error", "UV4 访问错误"),
    20: ("error", "未知错误"),
}

ACTION_FLAG = {
    "build": "-b",
    "rebuild": "-r",
    "clean": "-c",
    "flash": "-f",
}


def parse_log(log_path: str) -> dict:
    """解析构建日志，提取 errors/warnings/Program Size"""
    summary = {"errors": 0, "warnings": 0, "flash_bytes": 0, "ram_bytes": 0}
    if not os.path.isfile(log_path):
        return summary

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # 匹配摘要行: "x Error(s), y Warning(s)"
    m = re.search(r"(\d+)\s+Error\(s\)\s*,\s*(\d+)\s+Warning\(s\)", content)
    if m:
        summary["errors"] = int(m.group(1))
        summary["warnings"] = int(m.group(2))

    # 匹配 Program Size: Code=xxx RO-data=xxx RW-data=xxx ZI-data=xxx
    m = re.search(
        r"Program Size:\s+Code=(\d+)\s+RO-data=(\d+)\s+RW-data=(\d+)\s+ZI-data=(\d+)",
        content,
    )
    if m:
        code_size = int(m.group(1))
        ro_data = int(m.group(2))
        rw_data = int(m.group(3))
        zi_data = int(m.group(4))
        summary["flash_bytes"] = code_size + ro_data + rw_data
        summary["ram_bytes"] = rw_data + zi_data

    return summary


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    raw_path = (raw_path or "").strip()
    if not raw_path:
        return base_dir
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _collect_target_artifacts(project_path: Path, target: str) -> dict:
    """从 .uvprojx 中解析输出目录和名称，推断构建产物路径。"""
    if project_path.suffix.lower() != ".uvprojx":
        return {}

    try:
        root = ET.parse(str(project_path)).getroot()
    except (ET.ParseError, OSError):
        return {}

    target_el = None
    fallback_target = None
    for item in root.iter("Target"):
        name_el = item.find("TargetName")
        if name_el is None or not name_el.text:
            continue
        if fallback_target is None:
            fallback_target = item
        if target and name_el.text.strip() == target:
            target_el = item
            break

    if target_el is None:
        target_el = fallback_target
    if target_el is None:
        return {}

    common = target_el.find("TargetOption/TargetCommonOption")
    if common is None:
        return {}

    output_dir = _resolve_path(
        project_path.parent,
        common.findtext("OutputDirectory", default=""),
    )
    output_name = common.findtext("OutputName", default="").strip() or project_path.stem

    candidates = {
        "axf_file": output_dir / f"{output_name}.axf",
        "elf_file": output_dir / f"{output_name}.elf",
        "hex_file": output_dir / f"{output_name}.hex",
        "bin_file": output_dir / f"{output_name}.bin",
    }

    details = {}
    for key, path in candidates.items():
        if path.exists():
            details[key] = str(path.resolve())

    if not details and output_dir.exists():
        for suffix, key in (
            (".axf", "axf_file"),
            (".elf", "elf_file"),
            (".hex", "hex_file"),
            (".bin", "bin_file"),
        ):
            matches = sorted(
                output_dir.rglob(f"{output_name}*{suffix}"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if matches:
                details[key] = str(matches[0].resolve())

    debug_file = details.get("elf_file") or details.get("axf_file")
    flash_file = (
        details.get("hex_file")
        or details.get("bin_file")
        or details.get("elf_file")
        or details.get("axf_file")
    )
    if debug_file:
        details["debug_file"] = debug_file
    if flash_file:
        details["flash_file"] = flash_file
    if output_dir.exists():
        details["output_dir"] = str(output_dir)
    return details


def run_uv4(uv4_exe: str, action: str, project: str, target: str,
            log_dir: str, clean_first: bool = False) -> dict:
    """调用 UV4.exe 并返回结构化结果"""
    project_path = Path(project).resolve()
    if not project_path.exists():
        return {
            "status": "error",
            "action": action,
            "error": {"code": "project_not_found", "message": f"工程文件不存在: {project_path}"},
        }

    if not os.path.isfile(uv4_exe):
        return {
            "status": "error",
            "action": action,
            "error": {"code": "uv4_not_found", "message": f"UV4.exe 不存在: {uv4_exe}"},
        }

    # 准备日志目录和文件
    log_path = Path(log_dir).resolve()
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{project_path.stem}-{target}-{action}.log"

    # 构建命令
    flag = ACTION_FLAG[action]
    if action == "rebuild" and clean_first:
        flag = "-cr"

    cmd = [uv4_exe, flag, str(project_path), "-j0", "-o", str(log_file)]
    if target:
        cmd.extend(["-t", target])

    # 执行
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        errorlevel = proc.returncode
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "timeout", "message": "UV4.exe 执行超时(600s)"},
        }
    except Exception as e:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "exec_error", "message": str(e)},
        }

    # 解析结果
    el_status, el_msg = ERRORLEVEL_MAP.get(errorlevel, ("error", f"未知返回码: {errorlevel}"))
    summary = parse_log(str(log_file))

    # 根据 errorlevel 和日志综合判断状态
    if errorlevel >= 2:
        status = "error"
    elif summary["errors"] > 0:
        status = "error"
    else:
        status = "ok"

    artifact_details = _collect_target_artifacts(project_path, target)

    return {
        "status": status,
        "action": action,
        "summary": summary,
        "details": {
            "project": str(project_path),
            "target": target,
            "log_file": str(log_file),
            "errorlevel": errorlevel,
            "errorlevel_desc": el_msg,
            **artifact_details,
        },
    }


def check_last_build_ok(log_dir: str, project: str, target: str) -> bool:
    """检查最近一次构建是否成功（flash 前置条件）"""
    log_path = Path(log_dir).resolve()
    # 检查 build 和 rebuild 日志
    for action in ("build", "rebuild"):
        stem = Path(project).stem
        log_file = log_path / f"{stem}-{target}-{action}.log"
        if log_file.exists():
            summary = parse_log(str(log_file))
            if summary["errors"] == 0:
                return True
    return False


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Keil MDK 构建/重建/清理/烧录")
    parser.add_argument("action", choices=["build", "rebuild", "clean", "flash"])
    parser.add_argument("--uv4", required=True, help="UV4.exe 路径")
    parser.add_argument("--project", required=True, help="工程文件路径")
    parser.add_argument("--target", default="", help="Target 名称")
    parser.add_argument("--log-dir", default=".build", help="日志输出目录")
    parser.add_argument("--clean-first", action="store_true", help="rebuild 时先 clean")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # flash 前置检查
    if args.action == "flash":
        if not check_last_build_ok(args.log_dir, args.project, args.target):
            result = {
                "status": "error",
                "action": "flash",
                "error": {
                    "code": "build_not_clean",
                    "message": "最近一次构建存在错误或无构建记录，禁止继续烧录。请先执行 build 并确认无错误。",
                },
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr)
            sys.exit(1)

    result = run_uv4(
        uv4_exe=args.uv4,
        action=args.action,
        project=args.project,
        target=args.target,
        log_dir=args.log_dir,
        clean_first=args.clean_first,
    )

    if args.as_json:
        output_json(result)
    else:
        if result["status"] == "ok":
            s = result.get("summary", {})
            print(f"[{args.action}] 成功 — errors: {s.get('errors',0)}, warnings: {s.get('warnings',0)}")
            if s.get("flash_bytes"):
                print(f"  Flash: {s['flash_bytes']} bytes, RAM: {s['ram_bytes']} bytes")
            print(f"  日志: {result['details']['log_file']}")
        else:
            err = result.get("error", result.get("details", {}))
            msg = err.get("message", err.get("errorlevel_desc", "未知错误"))
            print(f"[{args.action}] 失败 — {msg}", file=sys.stderr)
            if "log_file" in result.get("details", {}):
                print(f"  日志: {result['details']['log_file']}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
