#!/usr/bin/env python3
"""基于 tshark 的抓包工具，支持保存文件、过滤、解码规则和结构化输出。"""

import argparse
import io
import json
import os
import subprocess
import sys
import signal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from net_runtime import (
    get_net_config,
    save_project_config,
    update_state_entry,
    check_tshark,
)


def build_tshark_cmd(config, args):
    exe = config["tshark_exe"]
    cmd = [exe]

    # 接口
    iface = config["interface"]
    if iface:
        cmd += ["-i", str(iface)]

    # 抓包过滤器 (BPF)
    capture_filter = config["capture_filter"]
    if capture_filter:
        cmd += ["-f", capture_filter]

    # 显示过滤器
    display_filter = config["display_filter"]
    if display_filter:
        cmd += ["-Y", display_filter]

    # 持续时间
    duration = config["duration"]
    cmd += ["-a", f"duration:{duration}"]

    # 输出文件
    if args.output:
        fmt = args.format or config["capture_format"]
        cmd += ["-w", args.output]
        if fmt == "pcap":
            cmd += ["-F", "pcap"]

    # 解码规则
    if args.decode_as:
        cmd += ["-d", args.decode_as]

    # JSON Lines 输出 (使用 -T ek)
    if args.output_json and not args.output:
        cmd += ["-T", "ek"]

    return cmd, exe


def main():
    parser = argparse.ArgumentParser(description="tshark 抓包")
    parser.add_argument("--interface", "-i", help="抓包接口")
    parser.add_argument("--duration", type=int, help="抓包时长(秒)")
    parser.add_argument("--capture-filter", "-f", help="抓包过滤器(BPF)")
    parser.add_argument("--display-filter", "-Y", help="显示过滤器")
    parser.add_argument("--output", "-o", default="", help="保存抓包文件路径")
    parser.add_argument("--format", choices=["pcapng", "pcap"], help="抓包文件格式")
    parser.add_argument("--decode-as", default="", help="自定义解码规则")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON Lines 输出")
    args = parser.parse_args()

    # 获取配置
    config, sources = get_net_config(
        cli_interface=args.interface,
        cli_duration=args.duration,
        cli_capture_filter=args.capture_filter,
        cli_display_filter=args.display_filter,
    )

    exe = config["tshark_exe"]

    if not check_tshark(exe):
        error = {
            "status": "error",
            "action": "capture",
            "error": {
                "code": "tshark_not_found",
                "message": f"未找到 tshark ({exe})，请确认 Wireshark 已安装且已加入 PATH",
            },
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    iface = config["interface"]
    if not iface:
        error = {
            "status": "error",
            "action": "capture",
            "error": {
                "code": "no_interface",
                "message": "未配置抓包接口，请用 --interface 指定或在 .embeddedskills/config.json 中配置",
            },
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 保存确认的配置
    save_project_config(values={
        "interface": iface,
        "duration": config["duration"],
        "capture_filter": config["capture_filter"],
        "display_filter": config["display_filter"],
    })

    cmd, _ = build_tshark_cmd(config, args)
    duration = config["duration"]

    print(f"[net capture] 接口={iface}, 时长={duration}s", file=sys.stderr)
    if config.get("default_capture_filter"):
        print(f"  抓包过滤器: {config['default_capture_filter']}", file=sys.stderr)
    if config.get("default_display_filter"):
        print(f"  显示过滤器: {config['default_display_filter']}", file=sys.stderr)
    if args.output:
        print(f"  输出文件: {args.output}", file=sys.stderr)
    print(f"  命令: {' '.join(cmd)}", file=sys.stderr)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # 实时输出 stdout
        for line in proc.stdout:
            print(line, end="")
        proc.wait()

        stderr_output = proc.stderr.read()
        if stderr_output.strip():
            print(stderr_output, file=sys.stderr)

        # 输出摘要
        summary = {"status": "ok", "action": "capture", "summary": f"抓包完成，时长 {duration}s"}
        if args.output and os.path.exists(args.output):
            size = os.path.getsize(args.output)
            summary["summary"] += f"，文件: {args.output} ({size} bytes)"
            summary["details"] = {"output_file": args.output, "file_size": size}

        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)

        # 更新状态
        update_state_entry("last_observe", {
            "type": "net_capture",
            "interface": iface,
            "duration": duration,
            "output_file": args.output if args.output and os.path.exists(args.output) else None,
        })

    except KeyboardInterrupt:
        proc.terminate()
        print("\n[net capture] 用户中断抓包", file=sys.stderr)
    except Exception as e:
        error = {
            "status": "error",
            "action": "capture",
            "error": {"code": "capture_failed", "message": str(e)},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
