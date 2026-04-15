#!/usr/bin/env python3
"""基于 tshark 的流量统计工具，按协议、端点或端口输出。"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from net_runtime import (
    decode_text,
    get_net_config,
    save_project_config,
    update_state_entry,
    check_tshark,
)


def run_tshark_stats(exe, iface, duration, mode, interval, display_filter=""):
    """先抓包到临时文件，再离线统计，确保显示过滤器可靠生效。"""
    fd, capture_file = tempfile.mkstemp(prefix="net_stats_", suffix=".pcapng")
    os.close(fd)
    filtered_file = ""

    capture_cmd = [exe, "-i", str(iface), "-a", f"duration:{duration}", "-w", capture_file]

    try:
        capture = subprocess.run(
            capture_cmd,
            capture_output=True,
            text=False,
            timeout=duration + 30,
        )
        if capture.returncode != 0:
            return "", decode_text(capture.stderr), capture.returncode

        analyze_source = capture_file
        if display_filter:
            fd, filtered_file = tempfile.mkstemp(prefix="net_stats_filtered_", suffix=".pcapng")
            os.close(fd)
            filter_cmd = [exe, "-r", capture_file, "-Y", display_filter, "-w", filtered_file]
            filtered = subprocess.run(
                filter_cmd,
                capture_output=True,
                text=False,
                timeout=60,
            )
            if filtered.returncode != 0:
                return "", decode_text(filtered.stderr), filtered.returncode
            analyze_source = filtered_file

        analyze_cmd = [exe, "-r", analyze_source, "-q"]
        if mode == "protocol":
            analyze_cmd += ["-z", "io,phs"]
        elif mode == "endpoint":
            analyze_cmd += ["-z", "endpoints,ip"]
        elif mode == "port":
            analyze_cmd += ["-z", "endpoints,tcp"]
        else:  # overview
            analyze_cmd += ["-z", f"io,stat,{interval}"]

        result = subprocess.run(
            analyze_cmd,
            capture_output=True,
            text=False,
            timeout=60,
        )
        return decode_text(result.stdout), decode_text(result.stderr), result.returncode
    except subprocess.TimeoutExpired:
        return "", "统计超时", -1
    except FileNotFoundError:
        return "", "tshark 未找到", -2
    finally:
        if os.path.exists(capture_file):
            os.remove(capture_file)
        if filtered_file and os.path.exists(filtered_file):
            os.remove(filtered_file)


def parse_io_stat(stdout):
    """解析 io,stat 输出。"""
    intervals = []
    for line in stdout.splitlines():
        m = re.match(r"\|\s*([\d.]+)\s*<>\s*([\d.]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", line)
        if m:
            intervals.append({
                "start": float(m.group(1)),
                "end": float(m.group(2)),
                "frames": int(m.group(3)),
                "bytes": int(m.group(4)),
            })
    return intervals


def parse_protocol_hierarchy(stdout):
    """解析 io,phs 输出。"""
    protocols = []
    for line in stdout.splitlines():
        line = line.strip()
        m = re.match(r"(\S+)\s+frames:(\d+)\s+bytes:(\d+)", line)
        if m:
            protocols.append({
                "protocol": m.group(1),
                "frames": int(m.group(2)),
                "bytes": int(m.group(3)),
            })
    return protocols


def parse_endpoints(stdout):
    """解析 endpoints 输出。"""
    endpoints = []
    started = False
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("=") or "Filter:" in line:
            started = True
            continue
        if not started or not line or "Address" in line or line.startswith("|"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) >= 3:
            endpoints.append({
                "address": parts[0],
                "packets": parts[1],
                "bytes": parts[2],
                "raw": line,
            })
    return endpoints


def main():
    parser = argparse.ArgumentParser(description="流量统计")
    parser.add_argument("--interface", "-i", help="抓包接口")
    parser.add_argument("--duration", type=int, help="统计时长(秒)")
    parser.add_argument("--display-filter", "-Y", help="显示过滤器")
    parser.add_argument("--interval", type=int, default=1, help="统计间隔(秒)")
    parser.add_argument("--mode", default="overview",
                        choices=["overview", "protocol", "endpoint", "port"])
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    args = parser.parse_args()

    # 获取配置
    config, sources = get_net_config(
        cli_interface=args.interface,
        cli_duration=args.duration,
        cli_display_filter=args.display_filter,
    )

    exe = config["tshark_exe"]
    iface = config["interface"]
    duration = config["duration"]
    display_filter = config["display_filter"]

    if not check_tshark(exe):
        error = {
            "status": "error",
            "action": "stats",
            "error": {
                "code": "tshark_not_found",
                "message": f"未找到 tshark ({exe})，请确认 Wireshark 已安装且已加入 PATH",
            },
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    if not iface:
        error = {
            "status": "error",
            "action": "stats",
            "error": {"code": "no_interface", "message": "未配置抓包接口，请用 --interface 指定或在 .embeddedskills/config.json 中配置"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 保存确认的配置
    save_project_config(values={
        "interface": iface,
        "duration": duration,
        "display_filter": display_filter,
    })

    print(f"[net stats] 接口={iface}, 时长={duration}s, 模式={args.mode}", file=sys.stderr)

    stdout, stderr, rc = run_tshark_stats(exe, iface, duration, args.mode, args.interval, display_filter)

    if rc != 0:
        error = {
            "status": "error",
            "action": "stats",
            "error": {"code": "stats_failed", "message": stderr.strip() or "统计失败"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = {
        "status": "ok",
        "action": "stats",
        "summary": {"duration_sec": duration, "mode": args.mode},
        "details": {},
    }

    if args.mode == "overview":
        intervals = parse_io_stat(stdout)
        total_frames = sum(i["frames"] for i in intervals)
        total_bytes = sum(i["bytes"] for i in intervals)
        result["summary"]["description"] = f"{duration}s 内共 {total_frames} 帧, {total_bytes} 字节"
        result["details"]["intervals"] = intervals
        result["details"]["total_frames"] = total_frames
        result["details"]["total_bytes"] = total_bytes

    elif args.mode == "protocol":
        protocols = parse_protocol_hierarchy(stdout)
        result["summary"]["description"] = f"检测到 {len(protocols)} 种协议"
        result["details"]["protocols"] = protocols

    elif args.mode in ("endpoint", "port"):
        endpoints = parse_endpoints(stdout)
        result["summary"]["description"] = f"发现 {len(endpoints)} 个端点"
        result["details"]["endpoints"] = endpoints

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[net stats] {result['summary'].get('description', '统计完成')}")
        details = result["details"]
        if "intervals" in details:
            print(f"\n  {'时间段':<20} {'帧数':<10} {'字节数'}")
            for i in details["intervals"]:
                print(f"  {i['start']:.0f}-{i['end']:.0f}s{'':<14} {i['frames']:<10} {i['bytes']}")
        if "protocols" in details:
            print("\n  协议分布:")
            for p in details["protocols"][:15]:
                print(f"    {p['protocol']}: {p['frames']} frames, {p['bytes']} bytes")
        if "endpoints" in details:
            print("\n  端点:")
            for e in details["endpoints"][:15]:
                print(f"    {e['address']}: {e['packets']} pkts, {e['bytes']} bytes")

    # 更新状态
    update_state_entry("last_observe", {
        "type": "net_stats",
        "interface": iface,
        "duration": duration,
        "mode": args.mode,
    })


if __name__ == "__main__":
    main()
