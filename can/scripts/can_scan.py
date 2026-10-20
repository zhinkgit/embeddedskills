"""CAN 接口扫描：区分检测到的设备与已注册的软件后端。"""

import argparse
import json
import sys

from can_runtime import list_registered_backends, scan_can_interfaces


def output_json(result):
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description="扫描可用 CAN 接口")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    interfaces, err = scan_can_interfaces()

    if err:
        result = {"status": "error", "action": "scan", "error": {"code": "import_error", "message": err}}
        if args.json:
            output_json(result)
        else:
            print(f"错误: {err}", file=sys.stderr)
        sys.exit(1)

    result = {
        "status": "ok",
        "action": "scan",
        "summary": f"发现 {len(interfaces)} 个 CAN 接口",
        "details": {"interfaces": interfaces, "registered_backends": list_registered_backends()},
    }

    if args.json:
        output_json(result)
    else:
        print("已注册后端（不代表已连接设备）: " + ", ".join(result["details"]["registered_backends"]))
        if not interfaces:
            print("未发现可用 CAN 接口")
        else:
            print(f"发现 {len(interfaces)} 个 CAN 接口:\n")
            for iface in interfaces:
                dev = f" [{iface['device']}]" if iface["device"] else ""
                vid_pid = f" (VID:{iface['vid']} PID:{iface['pid']})" if iface["vid"] else ""
                print(f"  {iface['interface']}:{iface['channel']}{dev}{vid_pid} — {iface['status']}")


if __name__ == "__main__":
    main()
