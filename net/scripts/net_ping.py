#!/usr/bin/env python3
"""连通性测试工具，支持 ICMP/TCP ping、批量测试和路由追踪。"""

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from net_runtime import (
    get_net_config,
    save_project_config,
    update_state_entry,
)


def icmp_ping(target, count=4, timeout_ms=1000):
    """使用系统 ping 命令做 ICMP 测试。"""
    system = platform.system()
    timeout_sec = max(1, (timeout_ms + 999) // 1000)
    if system == "Windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), target]
    elif system == "Linux":
        cmd = ["ping", "-n", "-c", str(count), "-W", str(timeout_sec), target]
    else:
        return {"target": target, "reachable": False, "error": f"ICMP ping 暂不支持此平台: {system}"}
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="gbk" if system == "Windows" else "utf-8", errors="replace",
            env={**os.environ, "LC_ALL": "C"}, timeout=count * (timeout_sec + 1) + 10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"target": target, "reachable": False, "error": f"ping 执行失败: {exc}"}

    output = result.stdout
    reachable = False
    sent = received = 0
    avg_ms = None

    for line in output.splitlines():
        # 统计行
        m = re.search(r"已发送\s*=\s*(\d+).*已接收\s*=\s*(\d+)", line)
        if not m:
            m = re.search(r"Sent\s*=\s*(\d+).*Received\s*=\s*(\d+)", line, re.IGNORECASE)
        if not m:
            m = re.search(r"(\d+) packets transmitted,\s*(\d+) (?:packets )?received", line)
        if m:
            sent = int(m.group(1))
            received = int(m.group(2))
            reachable = received > 0

        # 平均延迟
        m2 = re.search(r"平均\s*=\s*(\d+)ms", line)
        if not m2:
            m2 = re.search(r"Average\s*=\s*(\d+)ms", line, re.IGNORECASE)
        if m2:
            avg_ms = int(m2.group(1))
        linux_rtt = re.search(r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev)\s*=\s*[\d.]+/([\d.]+)/", line)
        if linux_rtt:
            avg_ms = float(linux_rtt.group(1))

    if sent == 0 or result.returncode not in (0, 1):
        return {
            "target": target, "reachable": False,
            "error": (result.stderr or output).strip() or f"ping 未返回有效统计，退出码 {result.returncode}",
        }

    return {
        "target": target,
        "reachable": reachable,
        "sent": sent,
        "received": received,
        "loss_rate": f"{((sent - received) / sent * 100):.0f}%" if sent > 0 else "N/A",
        "avg_ms": avg_ms,
    }


def tcp_ping(target, port, timeout_ms=1000):
    """TCP 连通性测试。"""
    timeout_sec = timeout_ms / 1000.0
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_sec)
        start = __import__("time").time()
        sock.connect((target, port))
        elapsed = (__import__("time").time() - start) * 1000
        sock.close()
        return {"target": target, "port": port, "reachable": True, "latency_ms": round(elapsed, 1)}
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return {"target": target, "port": port, "reachable": False, "error": str(e)}


def traceroute(target, timeout_ms=1000):
    """路由追踪。"""
    system = platform.system()
    if system == "Windows":
        cmd = ["tracert", "-d", "-w", str(timeout_ms), "-h", "30", target]
    elif system == "Linux":
        cmd = ["traceroute", "-n", "-w", str(timeout_ms / 1000.0), "-m", "30", target]
    else:
        return {"target": target, "hops": [], "error": f"路由追踪暂不支持此平台: {system}"}
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="gbk" if system == "Windows" else "utf-8", errors="replace",
            env={**os.environ, "LC_ALL": "C"}, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"target": target, "hops": [], "error": f"{cmd[0]} 执行失败: {exc}"}
    if result.returncode != 0:
        return {"target": target, "hops": [], "error": (result.stderr or result.stdout).strip() or f"{cmd[0]} 退出码 {result.returncode}"}

    hops = []
    for line in result.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(.+)", line)
        if m:
            hop_num = int(m.group(1))
            rest = m.group(2).strip()
            hops.append({"hop": hop_num, "detail": rest})

    reachable = any(target in hop["detail"] for hop in hops)
    return {"target": target, "hops": hops, "reachable": reachable}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="连通性测试")
    parser.add_argument("--target", "-t", help="目标地址")
    parser.add_argument("--tcp", type=int, default=0, help="TCP 端口")
    parser.add_argument("--count", type=int, default=4, help="ping 次数")
    parser.add_argument("--traceroute", action="store_true", help="路由追踪")
    parser.add_argument("--concurrent", type=int, default=4, help="并发线程数")
    parser.add_argument("--timeout", type=int, help="超时(毫秒)")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    args = parser.parse_args()

    # 获取配置
    config, sources = get_net_config(
        cli_target=args.target,
        cli_timeout_ms=args.timeout,
    )

    target = config["target"]
    timeout_ms = config["timeout_ms"]

    if args.count <= 0 or timeout_ms <= 0:
        parser.error("--count 和 --timeout 必须大于 0")

    if not target:
        error = {
            "status": "error",
            "action": "ping",
            "error": {"code": "no_target", "message": "未配置目标地址，请用 --target 指定或在 .embeddedskills/config.json 中配置"},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 保存确认的配置
    save_project_config(values={
        "target": target,
        "timeout_ms": timeout_ms,
    })

    # 支持逗号分隔的多目标
    targets = [t.strip() for t in target.split(",") if t.strip()]

    results = []

    if args.traceroute:
        for t in targets:
            results.append(traceroute(t, timeout_ms))
    elif args.tcp > 0:
        with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {pool.submit(tcp_ping, t, args.tcp, timeout_ms): t for t in targets}
            for future in as_completed(futures):
                results.append(future.result())
    else:
        with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {pool.submit(icmp_ping, t, args.count, timeout_ms): t for t in targets}
            for future in as_completed(futures):
                results.append(future.result())

    reachable_count = sum(1 for r in results if r.get("reachable", False))
    total = len(results)
    action = "traceroute" if args.traceroute else ("tcp_ping" if args.tcp > 0 else "ping")
    execution_failed = action != "tcp_ping" and any(r.get("error") for r in results)

    output = {
        "status": "error" if execution_failed else "ok",
        "action": action,
        "summary": {
            "total": total,
            "reachable": reachable_count,
            "description": f"{reachable_count}/{total} 目标可达",
        },
        "details": {"results": results},
    }
    if execution_failed:
        output["error"] = {"code": "command_failed", "message": "网络诊断命令执行失败，详见 results"}

    if args.output_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"[net {action}] {output['summary']['description']}")
        for r in results:
            if args.traceroute:
                print(f"\n  追踪 {r['target']}:")
                for h in r.get("hops", []):
                    print(f"    {h['hop']:>3}  {h['detail']}")
                if r.get("error"):
                    print(f"    {r['error']}")
            else:
                icon = "+" if r.get("reachable") else "x"
                line = f"  [{icon}] {r['target']}"
                if r.get("port"):
                    line += f":{r['port']}"
                if r.get("avg_ms") is not None:
                    line += f"  延迟={r['avg_ms']}ms"
                elif r.get("latency_ms") is not None:
                    line += f"  延迟={r['latency_ms']}ms"
                if r.get("loss_rate"):
                    line += f"  丢包={r['loss_rate']}"
                if r.get("error"):
                    line += f"  ({r['error']})"
                print(line)

    if execution_failed:
        sys.exit(1)

    # 更新状态
    update_state_entry("last_net_ping", {
        "target": target,
        "reachable_count": reachable_count,
        "total": total,
    })


if __name__ == "__main__":
    main()
