#!/usr/bin/env python3
"""网络接口发现工具，可关联 tshark 抓包接口列表。"""

import argparse
import io
import json
import sys

# 确保 stdout 使用 UTF-8 编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from net_runtime import parse_ipconfig, parse_tshark_interfaces


def main():
    parser = argparse.ArgumentParser(description="列出网络接口")
    parser.add_argument("--filter", default="", help="按关键词筛选接口")
    parser.add_argument("--tshark", action="store_true", help="同时显示 tshark 抓包接口")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 输出")
    parser.add_argument("--tshark-exe", default="tshark", help="tshark 路径")
    args = parser.parse_args()

    interfaces = parse_ipconfig()

    if args.filter:
        kw = args.filter.lower()
        interfaces = [
            iface for iface in interfaces
            if kw in iface["name"].lower()
            or kw in iface["description"].lower()
            or kw in iface["type"].lower()
            or kw in iface.get("ipv4", "").lower()
            or any(kw in ip.lower() for ip in iface.get("ipv4_list", []))
        ]

    result = {
        "status": "ok",
        "action": "iface",
        "summary": f"发现 {len(interfaces)} 个网络接口",
        "details": {
            "interfaces": interfaces,
        },
    }

    if args.tshark:
        tshark_ifaces = parse_tshark_interfaces(args.tshark_exe)
        if tshark_ifaces is None:
            result["details"]["tshark_interfaces"] = []
            result["details"]["tshark_note"] = "tshark 不可用或未找到"
        else:
            result["details"]["tshark_interfaces"] = tshark_ifaces

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[net iface] {result['summary']}")
        for iface in interfaces:
            status_icon = "●" if iface["status"] == "up" else "○"
            print(f"  {status_icon} {iface['name']} ({iface['type']})")
            if iface["description"]:
                print(f"    描述: {iface['description']}")
            ipv4_list = iface.get("ipv4_list") or ([iface["ipv4"]] if iface["ipv4"] else [])
            subnet_list = iface.get("subnet_list") or ([iface["subnet"]] if iface["subnet"] else [])
            if ipv4_list:
                paired = []
                for index, ip in enumerate(ipv4_list):
                    subnet = subnet_list[index] if index < len(subnet_list) else iface.get("subnet", "")
                    paired.append(f"{ip}/{subnet}" if subnet else ip)
                print(f"    IPv4: {', '.join(paired)}")
            if iface["mac"]:
                print(f"    MAC:  {iface['mac']}")
            gateway_list = iface.get("gateway_list") or ([iface["gateway"]] if iface["gateway"] else [])
            if gateway_list:
                print(f"    网关: {', '.join(gateway_list)}")
        if args.tshark and result["details"].get("tshark_interfaces"):
            print("\n[tshark 抓包接口]")
            for ti in result["details"]["tshark_interfaces"]:
                print(f"  {ti['index']}. {ti['device']}")
                if ti["description"]:
                    print(f"     {ti['description']}")


if __name__ == "__main__":
    main()
