#!/usr/bin/env python3
"""基于 tshark 的抓包工具，支持保存文件、过滤、解码规则和结构化输出。"""

import io
import json
import os
import subprocess
import sys
import signal
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from net_runtime import (
    decode_text,
    get_net_config,
    save_project_config,
    update_state_entry,
    check_tshark,
    ArgumentParser,
    run_cli,
    output_json,
    now_iso,
)


def build_tshark_cmd(config, args, *, output_path="", include_display_filter=True):
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
    if display_filter and include_display_filter:
        cmd += ["-Y", display_filter]

    # 持续时间
    duration = config["duration"]
    cmd += ["-a", f"duration:{duration}"]

    # 输出文件
    if output_path:
        fmt = args.format or config["capture_format"]
        cmd += ["-w", output_path]
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
    parser = ArgumentParser(description="tshark 抓包")
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
        output_json(error)
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
        output_json(error)
        sys.exit(1)

    # 保存确认的配置
    save_project_config(values={
        "interface": iface,
        "duration": config["duration"],
        "capture_filter": config["capture_filter"],
        "display_filter": config["display_filter"],
    })

    filter_after_capture = bool(args.output and config.get("display_filter"))
    temp_output = ""
    output_path = args.output
    if filter_after_capture:
        fd, temp_output = tempfile.mkstemp(
            prefix="net_capture_",
            suffix=".pcap" if (args.format or config["capture_format"]) == "pcap" else ".pcapng",
        )
        os.close(fd)
        output_path = temp_output

    cmd, _ = build_tshark_cmd(
        config,
        args,
        output_path=output_path,
        include_display_filter=not filter_after_capture,
    )
    duration = config["duration"]

    print(f"[net capture] 接口={iface}, 时长={duration}s", file=sys.stderr)
    if config.get("capture_filter"):
        print(f"  抓包过滤器: {config['capture_filter']}", file=sys.stderr)
    if config.get("display_filter"):
        print(f"  显示过滤器: {config['display_filter']}", file=sys.stderr)
    if args.output:
        print(f"  输出文件: {args.output}", file=sys.stderr)
        if filter_after_capture:
            print("  保存策略: 先原始抓包，再按显示过滤器离线筛选", file=sys.stderr)
    print(f"  命令: {' '.join(cmd)}", file=sys.stderr)

    proc = None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False
        )
        stdout_data, stderr_data = proc.communicate()
        stdout_text = decode_text(stdout_data)
        stderr_output = decode_text(stderr_data)

        if args.output_json and not args.output:
            for line in stdout_text.splitlines():
                if line.strip():
                    data = json.loads(line)
                    event = "capture_index" if "index" in data else "packet"
                    output_json({"event": event, "timestamp": data.get("timestamp") or now_iso(),
                                 "data": data}, indent=None)
        elif not args.output_json and stdout_text:
            print(stdout_text, end="")

        if proc.returncode != 0:
            error = {
                "status": "error",
                "action": "capture",
                "error": {
                    "code": "capture_failed",
                    "message": stderr_output.strip() or f"tshark 退出码 {proc.returncode}",
                },
            }
            output_json({**error, "event": "error", "timestamp": now_iso()}, indent=None)
            sys.exit(1)

        if filter_after_capture:
            filter_cmd = [exe, "-r", temp_output, "-Y", config["display_filter"], "-w", args.output]
            if (args.format or config["capture_format"]) == "pcap":
                filter_cmd += ["-F", "pcap"]
            if args.decode_as:
                filter_cmd += ["-d", args.decode_as]

            filtered = subprocess.run(
                filter_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            filtered_stderr = decode_text(filtered.stderr)
            if filtered.returncode != 0:
                error = {
                    "status": "error",
                    "action": "capture",
                    "error": {
                        "code": "capture_filter_failed",
                        "message": filtered_stderr.strip() or f"过滤失败，退出码 {filtered.returncode}",
                    },
                }
                output_json(error)
                sys.exit(1)
            if filtered_stderr.strip():
                print(filtered_stderr, file=sys.stderr)

        if stderr_output.strip():
            print(stderr_output, file=sys.stderr)

        # 输出摘要
        summary = {"status": "ok", "action": "capture", "summary": f"抓包完成，时长 {duration}s"}
        if args.output and os.path.exists(args.output):
            size = os.path.getsize(args.output)
            summary["summary"] += f"，文件: {args.output} ({size} bytes)"
            summary["details"] = {"output_file": args.output, "file_size": size}

        if args.output_json and not args.output:
            output_json({**summary, "event": "summary", "timestamp": now_iso()}, indent=None)
        else:
            output_json(summary, stream=sys.stdout if args.output_json else sys.stderr)

        # 更新状态
        update_state_entry("last_observe", {
            "type": "net_capture",
            "interface": iface,
            "duration": duration,
            "output_file": args.output if args.output and os.path.exists(args.output) else None,
        })

    except KeyboardInterrupt:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait()
        if args.output_json:
            output_json({"status": "error", "action": "capture", "event": "error", "timestamp": now_iso(),
                         "error": {"code": "interrupted", "message": "用户中断抓包"}}, indent=None)
        else:
            print("\n[net capture] 用户中断抓包", file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError) as e:
        error = {
            "status": "error",
            "action": "capture",
            "error": {"code": "capture_failed", "message": str(e)},
        }
        output_json({**error, "event": "error", "timestamp": now_iso()}, indent=None)
        sys.exit(1)
    finally:
        if temp_output and os.path.exists(temp_output):
            os.remove(temp_output)


if __name__ == "__main__":
    run_cli(main)
