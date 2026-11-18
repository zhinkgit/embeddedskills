"""EIDE ELF size analysis using arm-none-eabi-size."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from shutil import which

from eide_runtime import ArgumentParser, load_local_config, output_json, run_cli


def _find_size_tool(prefix: str) -> str:
    """Find the size tool from the toolchain prefix."""
    tool = which(prefix + "size")
    if tool:
        return str(Path(tool).resolve())

    alt_prefixes = ["arm-none-eabi-", "arm-eabi-", "arm-elf-"]
    for p in alt_prefixes:
        tool = which(p + "size")
        if tool:
            return str(Path(tool).resolve())
    return prefix + "size"


def run_size_analyze(
    elf_path: str, size_tool: str
) -> dict:
    """Run size tool and parse output."""
    elf = Path(elf_path).resolve()
    if not elf.is_file():
        return {
            "status": "error",
            "action": "size_analyze",
            "error": {
                "code": "elf_not_found",
                "message": f"ELF file not found: {elf_path}",
            },
        }

    try:
        proc = subprocess.run(
            [size_tool, str(elf)],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "action": "size_analyze",
            "error": {
                "code": "timeout",
                "message": "size tool timed out",
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "action": "size_analyze",
            "error": {"code": "exec_error", "message": str(exc)},
        }

    output = proc.stdout or ""
    if proc.returncode != 0:
        return {
            "status": "error",
            "action": "size_analyze",
            "error": {
                "code": "size_failed",
                "message": (
                    proc.stderr or "size tool failed"
                ),
            },
        }

    sections = _parse_size_output(output)

    flash_bytes = (
        sections.get("text", 0)
        + sections.get("data", 0)
    )
    ram_bytes = (
        sections.get("data", 0) + sections.get("bss", 0)
    )
    total = flash_bytes + sections.get("bss", 0)

    metrics = {
        "text": sections.get("text", 0),
        "data": sections.get("data", 0),
        "bss": sections.get("bss", 0),
        "dec": sections.get("dec", total),
        "hex": sections.get("hex", ""),
        "flash_bytes": flash_bytes,
        "ram_bytes": ram_bytes,
    }
    if "filename" in sections:
        metrics["filename"] = sections["filename"]

    return {
        "status": "ok",
        "action": "size_analyze",
        "details": {
            "elf_file": str(elf),
            "size_tool": size_tool,
        },
        "metrics": metrics,
    }


def _parse_size_output(output: str) -> dict:
    """Parse GNU size output."""
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    if not lines:
        return {}

    sections: dict = {}
    for line in lines:
        match = re.match(
            r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9a-fA-F]+)\s+(.+)",
            line,
        )
        if match:
            sections["text"] = int(match.group(1))
            sections["data"] = int(match.group(2))
            sections["bss"] = int(match.group(3))
            sections["dec"] = int(match.group(4))
            sections["hex"] = match.group(5)
            sections["filename"] = match.group(6).strip()
            return sections

    # Fallback: Berkeley format
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            try:
                sections["text"] = int(parts[0])
                sections["data"] = int(parts[1])
                sections["bss"] = int(parts[2])
                if len(parts) >= 4:
                    sections["dec"] = int(parts[3])
                if len(parts) >= 5:
                    sections["hex"] = parts[4]
                if len(parts) >= 6:
                    sections["filename"] = parts[5]
                return sections
            except ValueError:
                continue
    return {}


def run_size_compare(
    elf1_path: str, elf2_path: str, size_tool: str
) -> dict:
    """Compare two ELF files and return delta."""
    r1 = run_size_analyze(elf1_path, size_tool)
    r2 = run_size_analyze(elf2_path, size_tool)

    if r1["status"] != "ok":
        return {
            "status": "error",
            "action": "size_compare",
            "error": {
                "code": "analyze_failed",
                "message": (
                    f"Failed to analyze {elf1_path}: "
                    f"{r1.get('error', {}).get('message', '')}"
                ),
            },
        }
    if r2["status"] != "ok":
        return {
            "status": "error",
            "action": "size_compare",
            "error": {
                "code": "analyze_failed",
                "message": (
                    f"Failed to analyze {elf2_path}: "
                    f"{r2.get('error', {}).get('message', '')}"
                ),
            },
        }

    m1 = r1.get("metrics", {})
    m2 = r2.get("metrics", {})

    def _delta(a: int, b: int) -> int:
        return b - a

    delta_metrics = {
        "text_delta": _delta(
            m1.get("text", 0), m2.get("text", 0)
        ),
        "data_delta": _delta(
            m1.get("data", 0), m2.get("data", 0)
        ),
        "bss_delta": _delta(
            m1.get("bss", 0), m2.get("bss", 0)
        ),
        "flash_bytes_delta": _delta(
            m1.get("flash_bytes", 0),
            m2.get("flash_bytes", 0),
        ),
        "ram_bytes_delta": _delta(
            m1.get("ram_bytes", 0),
            m2.get("ram_bytes", 0),
        ),
    }

    return {
        "status": "ok",
        "action": "size_compare",
        "details": {
            "elf1": elf1_path,
            "elf2": elf2_path,
        },
        "metrics": {
            "before": {
                k: m1.get(k)
                for k in ("text", "data", "bss", "flash_bytes", "ram_bytes")
            },
            "after": {
                k: m2.get(k)
                for k in ("text", "data", "bss", "flash_bytes", "ram_bytes")
            },
            "delta": delta_metrics,
        },
    }


def main() -> None:
    parser = ArgumentParser(
        description="EIDE ELF size analysis"
    )
    sub = parser.add_subparsers(dest="command")

    analyze_p = sub.add_parser("analyze", help="Analyze ELF file size")
    analyze_p.add_argument("--elf", required=True, help="ELF file path")
    analyze_p.add_argument(
        "--toolchain-prefix",
        default=None,
        help="Toolchain prefix, default arm-none-eabi-",
    )
    analyze_p.add_argument("--json", action="store_true", dest="as_json")

    compare_p = sub.add_parser(
        "compare", help="Compare two ELF files"
    )
    compare_p.add_argument("--elf", required=True, help="Primary ELF")
    compare_p.add_argument(
        "--compare", required=True, help="Comparison ELF"
    )
    compare_p.add_argument(
        "--toolchain-prefix",
        default=None,
        help="Toolchain prefix",
    )
    compare_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    local_config = load_local_config(__file__)
    prefix = (
        args.toolchain_prefix
        or local_config.get("toolchain_prefix")
        or "arm-none-eabi-"
    )
    size_tool = _find_size_tool(prefix)

    if args.command == "analyze":
        result = run_size_analyze(args.elf, size_tool)
        if args.as_json:
            output_json(result)
            if result["status"] == "error":
                sys.exit(1)
        else:
            if result["status"] == "ok":
                m = result["metrics"]
                print(f"ELF: {result['details']['elf_file']}")
                print(
                    f"  text={m.get('text', 0):>8} B"
                )
                print(
                    f"  data={m.get('data', 0):>8} B"
                )
                print(
                    f"   bss={m.get('bss', 0):>8} B"
                )
                print(
                    f" Flash={m.get('flash_bytes', 0):>8} B"
                    f"  (text+data)"
                )
                print(
                    f"   RAM={m.get('ram_bytes', 0):>8} B"
                    f"  (data+bss)"
                )
            else:
                print(
                    f"Error: {result['error']['message']}",
                    file=sys.stderr,
                )
                sys.exit(1)

    elif args.command == "compare":
        result = run_size_compare(
            args.elf, args.compare, size_tool
        )
        if args.as_json:
            output_json(result)
            if result["status"] == "error":
                sys.exit(1)
        else:
            if result["status"] == "ok":
                m = result["metrics"]
                print("ELF Size Comparison:")
                print(
                    "          Before        After       Delta"
                )
                for key, label in (
                    ("text", "text "),
                    ("data", "data "),
                    ("bss", "bss  "),
                    ("flash_bytes", "Flash"),
                    ("ram_bytes", "RAM  "),
                ):
                    b = m["before"].get(key, 0)
                    a = m["after"].get(key, 0)
                    d = m["delta"].get(f"{key}_delta", a - b)
                    sign = "+" if d > 0 else ""
                    print(
                        f"  {label}: {b:>8}  {a:>8}  "
                        f"{sign}{d:>8}"
                    )
            else:
                print(
                    f"Error: {result['error']['message']}",
                    file=sys.stderr,
                )
                sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    run_cli(main)
