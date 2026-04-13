"""J-Link SWO 观测包装层。

说明：
- J-Link 不同版本的 SWO CLI 工具名和参数差异较大。
- 本脚本不硬编码具体 viewer，可通过 --viewer-cmd 或 config.json 中的 swo_command 提供完整命令。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jlink_runtime import (  # noqa: E402
    default_config_path,
    emit_stream_record,
    load_json_file,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    update_state_entry,
    workspace_root,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="J-Link SWO 输出捕获")
    parser.add_argument("--viewer-cmd", nargs="+", default=None, help="完整 SWO 采集命令，例如 JLinkSWOViewerCL 的调用参数")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))
    config = load_json_file(config_path)
    viewer_cmd = args.viewer_cmd or config.get("swo_command")

    if not viewer_cmd:
        message = "缺少 SWO viewer 命令，请通过 --viewer-cmd 或 jlink/config.json.swo_command 提供"
        result = make_result(
            status="error",
            action="swo",
            summary=message,
            details={},
            context=parameter_context(provider="jlink", workspace=str(workspace), config_path=config_path),
            error={"code": "missing_viewer_cmd", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    proc = None
    try:
        proc = subprocess.Popen(
            viewer_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        update_state_entry(
            "last_observe",
            {
                "provider": "jlink",
                "action": "swo",
                "channel_type": "swo",
                "stream_type": "text",
                "source": "jlink",
            },
            str(workspace),
        )

        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.02)
                continue
            emit_stream_record(source="jlink", channel_type="swo", text=line, as_json=args.as_json, stream_type="text")

    except FileNotFoundError:
        message = f"无法启动 SWO viewer: {viewer_cmd[0]}"
        result = make_result(
            status="error",
            action="swo",
            summary=message,
            details={"viewer_cmd": viewer_cmd},
            context=parameter_context(provider="jlink", workspace=str(workspace), config_path=config_path),
            error={"code": "viewer_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
