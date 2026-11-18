"""EIDE project scanner and configuration enumerator.

Parses .eide/eide.yml to discover projects and list build configurations.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from eide_runtime import ArgumentParser, run_cli, output_json


EIDE_YML = ".eide" + os.sep + "eide.yml"


def _load_yaml(path: str) -> dict:
    """Load a YAML file, trying PyYAML first then falling back to a
    built-in minimal parser for the subset of YAML that eide.yml uses."""
    try:
        import yaml  # type: ignore[import-untyped]
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass
    return _parse_simple_yaml(path)


def _parse_simple_yaml(path: str) -> dict:
    """Minimal YAML parser for eide.yml structure.

    Handles the subset of YAML that EIDE uses: scalars, basic lists,
    and nested maps. Not a general-purpose YAML parser.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    result: dict[str, Any] = {}
    stack: list[tuple[dict, int]] = [(result, -1)]
    current_list: list | None = None
    current_list_parent: dict | None = None
    current_list_key: str | None = None

    for line in lines:
        stripped = line.rstrip("\n\r")
        if not stripped or stripped.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if stripped.lstrip().startswith("- "):
            value_text = stripped.lstrip()[2:].strip()
            parsed_value = _parse_yaml_value(value_text)
            while stack and stack[-1][1] >= indent:
                stack.pop()
            if current_list is None:
                parent, _ = stack[-1]
                if parsed_value is not None:
                    if current_list_key and current_list_parent is not None:
                        if current_list_key not in current_list_parent:
                            current_list_parent[current_list_key] = []
                        current_list_parent[current_list_key].append(parsed_value)
            else:
                if parsed_value is not None:
                    current_list.append(parsed_value)
            continue

        if ":" in stripped:
            colon_idx = stripped.index(":")
            key = stripped[:colon_idx].strip()
            value_text = stripped[colon_idx + 1:].strip()
            parsed_value = _parse_yaml_value(value_text)

            while stack and stack[-1][1] >= indent:
                stack.pop()

            if parsed_value is not None:
                parent, _ = stack[-1] if stack else (result, -1)
                parent[key] = parsed_value
                current_list = None
                current_list_key = None
                current_list_parent = None
            elif value_text == "" or not value_text:
                parent, _ = stack[-1] if stack else (result, -1)
                new_map: dict = {}
                parent[key] = new_map
                stack.append((new_map, indent))
                current_list = None
                current_list_key = None
                current_list_parent = None
            else:
                parent, _ = stack[-1] if stack else (result, -1)
                if value_text == "[]":
                    parent[key] = []
                    current_list = parent[key]
                    current_list_key = key
                    current_list_parent = parent
                else:
                    parent[key] = _parse_yaml_value(value_text)

    return result


def _parse_yaml_value(text: str) -> Any:
    """Parse a YAML scalar value."""
    if not text:
        return None
    if text in ("true", "True", "TRUE", "yes", "Yes", "YES"):
        return True
    if text in ("false", "False", "FALSE", "no", "No", "NO"):
        return False
    if text in ("null", "Null", "NULL", "~"):
        return None
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def scan_projects(root: str) -> list[dict]:
    """Recursively search for directories containing .eide/eide.yml."""
    root_path = Path(root).resolve()
    projects = []
    for p in root_path.rglob(EIDE_YML):
        project_dir = p.parents[1]
        try:
            eide_data = _load_yaml(str(p))
        except Exception:
            eide_data = {}
        name = eide_data.get("name", project_dir.name)
        device = eide_data.get("deviceName", "")
        proj_type = eide_data.get("type", "")
        projects.append({
            "path": str(project_dir),
            "name": str(name),
            "device": str(device),
            "type": str(proj_type),
            "yaml_file": str(p),
        })
    projects.sort(key=lambda x: x["path"])
    return projects


def list_configs(project_path: str) -> list[dict]:
    """Extract build configurations from eide.yml.

    Each config is identified by its ConfigName from the builder.params
    env section, or derived from the project structure.
    """
    p = Path(project_path).resolve()
    yml_path = p / EIDE_YML
    if not yml_path.exists():
        raise FileNotFoundError(f"EIDE project file not found: {yml_path}")

    # Check builder.params first for configuration info
    builder_params_path = p / "build"
    configs = []

    if builder_params_path.exists():
        for build_dir in sorted(builder_params_path.iterdir()):
            bp_file = build_dir / "builder.params"
            if bp_file.is_file():
                try:
                    bp_data = load_json_file(str(bp_file))
                    config_name = build_dir.name
                    if bp_data:
                        config_name = bp_data.get("target", config_name)
                    configs.append({
                        "name": config_name,
                        "build_dir": str(build_dir),
                        "builder_params": str(bp_file),
                    })
                except Exception:
                    pass

    if not configs:
        try:
            eide_data = _load_yaml(str(yml_path))
        except Exception:
            eide_data = {}
        name = eide_data.get("name", p.name)
        configs.append({
            "name": name,
            "build_dir": "",
            "builder_params": "",
        })

    return configs


def load_json_file(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    parser = ArgumentParser(
        description="EIDE project scanner and config enumeration"
    )
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Search for EIDE projects")
    scan_p.add_argument("--root", default=".", help="Search root directory")
    scan_p.add_argument("--json", action="store_true", dest="as_json")

    configs_p = sub.add_parser("configs", help="List build configurations")
    configs_p.add_argument(
        "--project", required=True, help="Project root directory"
    )
    configs_p.add_argument("--json", action="store_true", dest="as_json")

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
                print("No EIDE projects found")
            else:
                print(f"Found {len(projects)} project(s):")
                for i, p in enumerate(projects, 1):
                    extra = ""
                    if p.get("device"):
                        extra = f" [{p['device']}]"
                    print(f"  {i}. {p['name']}{extra} — {p['path']}")

    elif args.command == "configs":
        try:
            configs = list_configs(args.project)
            result = {
                "status": "ok",
                "action": "configs",
                "details": {
                    "project": args.project,
                    "configs": configs,
                    "count": len(configs),
                },
            }
            if args.as_json:
                output_json(result)
            else:
                if not configs:
                    print("No build configurations found")
                else:
                    print(
                        f"Project {args.project} has "
                        f"{len(configs)} configuration(s):"
                    )
                    for i, c in enumerate(configs, 1):
                        print(f"  {i}. {c['name']}")
        except (FileNotFoundError, ValueError) as e:
            result = {
                "status": "error",
                "action": "configs",
                "error": {"code": "invalid_project", "message": str(e)},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    run_cli(main)
