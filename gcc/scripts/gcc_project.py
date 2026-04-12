"""GCC 嵌入式工程扫描与 CMake preset 枚举"""

import argparse
import json
import re
import sys
from pathlib import Path

EXCLUDE_DIRS = {"build", ".git", "node_modules", "__pycache__", ".vscode"}


def scan_projects(root: str) -> list[dict]:
    """递归搜索含 CMakeLists.txt 的嵌入式 CMake 工程"""
    root_path = Path(root).resolve()
    projects = []

    for cmake_file in root_path.rglob("CMakeLists.txt"):
        # 排除构建目录等
        if any(part in EXCLUDE_DIRS for part in cmake_file.parts):
            continue
        proj_dir = cmake_file.parent

        # 检查嵌入式特征：CMakePresets.json 或 cmake/ 下含工具链文件
        has_presets = (proj_dir / "CMakePresets.json").exists()
        has_toolchain = _has_embedded_toolchain(proj_dir)

        if not has_presets and not has_toolchain:
            continue

        # 提取项目名
        name = _extract_project_name(cmake_file) or proj_dir.name

        projects.append({
            "path": str(proj_dir),
            "name": name,
            "has_presets": has_presets,
        })

    projects.sort(key=lambda x: x["path"])
    return projects


def _has_embedded_toolchain(proj_dir: Path) -> bool:
    """检查是否有嵌入式工具链文件"""
    cmake_dir = proj_dir / "cmake"
    if not cmake_dir.is_dir():
        return False
    for f in cmake_dir.iterdir():
        if f.suffix == ".cmake" and f.is_file():
            try:
                content = f.read_text(encoding="utf-8", errors="replace").lower()
                if "arm-none-eabi" in content or "cross" in content:
                    return True
            except OSError:
                pass
    return False


def _extract_project_name(cmake_file: Path) -> str:
    """从 CMakeLists.txt 提取 project(NAME) 中的名称"""
    try:
        content = cmake_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"project\s*\(\s*(\w+)", content, re.IGNORECASE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return ""


def list_presets(project_dir: str) -> dict:
    """读取 CMakePresets.json，列出 configure 和 build preset"""
    proj_path = Path(project_dir).resolve()
    presets_file = proj_path / "CMakePresets.json"

    if not presets_file.exists():
        raise FileNotFoundError(f"CMakePresets.json 不存在: {presets_file}")

    data = json.loads(presets_file.read_text(encoding="utf-8"))

    # 合并 CMakeUserPresets.json（如果存在）
    user_presets_file = proj_path / "CMakeUserPresets.json"
    if user_presets_file.exists():
        user_data = json.loads(user_presets_file.read_text(encoding="utf-8"))
        data.setdefault("configurePresets", []).extend(
            user_data.get("configurePresets", [])
        )
        data.setdefault("buildPresets", []).extend(
            user_data.get("buildPresets", [])
        )

    source_dir = str(proj_path)
    all_presets = {cp["name"]: cp for cp in data.get("configurePresets", [])}

    def _resolve_inherited(preset: dict, field: str) -> str:
        """沿 inherits 链查找字段值"""
        val = preset.get(field, "")
        if val:
            return val
        inherits = preset.get("inherits")
        if inherits and isinstance(inherits, str):
            parent = all_presets.get(inherits)
            if parent:
                return _resolve_inherited(parent, field)
        return ""

    def _resolve_cache_vars(preset: dict) -> dict:
        """沿 inherits 链合并 cacheVariables"""
        cache_vars = dict(preset.get("cacheVariables", {}))
        inherits = preset.get("inherits")
        if inherits and isinstance(inherits, str):
            parent = all_presets.get(inherits)
            if parent:
                parent_vars = _resolve_cache_vars(parent)
                parent_vars.update(cache_vars)
                cache_vars = parent_vars
        return cache_vars

    configure_presets = []
    for p in data.get("configurePresets", []):
        if p.get("hidden", False):
            continue

        binary_dir = _resolve_inherited(p, "binaryDir")
        binary_dir = binary_dir.replace("${sourceDir}", source_dir)
        binary_dir = binary_dir.replace("${presetName}", p["name"])

        generator = _resolve_inherited(p, "generator")
        cache_vars = _resolve_cache_vars(p)

        configure_presets.append({
            "name": p["name"],
            "build_type": cache_vars.get("CMAKE_BUILD_TYPE", ""),
            "generator": generator,
            "binary_dir": binary_dir,
        })

    build_presets = []
    for p in data.get("buildPresets", []):
        if p.get("hidden", False):
            continue
        build_presets.append({
            "name": p["name"],
            "configure_preset": p.get("configurePreset", ""),
        })

    return {
        "project": str(proj_path),
        "configure_presets": configure_presets,
        "build_presets": build_presets,
    }


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="GCC 嵌入式工程扫描与 preset 枚举")
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="搜索嵌入式 CMake 工程")
    scan_p.add_argument("--root", default=".", help="搜索根目录")
    scan_p.add_argument("--json", action="store_true", dest="as_json")

    presets_p = sub.add_parser("presets", help="列出 CMake preset")
    presets_p.add_argument("--project", required=True, help="工程目录路径")
    presets_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    if args.command == "scan":
        projects = scan_projects(args.root)
        result = {
            "status": "ok",
            "action": "scan",
            "details": {"projects": projects, "count": len(projects)},
        }
        if args.as_json:
            output_json(result)
        else:
            if not projects:
                print("未找到嵌入式 CMake 工程")
            else:
                print(f"找到 {len(projects)} 个工程：")
                for p in projects:
                    preset_tag = " [presets]" if p["has_presets"] else ""
                    print(f"  {p['name']}{preset_tag} — {p['path']}")

    elif args.command == "presets":
        try:
            details = list_presets(args.project)
            result = {
                "status": "ok",
                "action": "presets",
                "details": details,
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"工程: {details['project']}")
                print("Configure presets:")
                for p in details["configure_presets"]:
                    print(f"  - {p['name']} ({p['build_type']}) -> {p['binary_dir']}")
                if details["build_presets"]:
                    print("Build presets:")
                    for p in details["build_presets"]:
                        print(f"  - {p['name']} (configure: {p['configure_preset']})")
        except (FileNotFoundError, ValueError) as e:
            result = {
                "status": "error",
                "action": "presets",
                "error": {"code": "invalid_project", "message": str(e)},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
