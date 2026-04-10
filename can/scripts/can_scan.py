"""CAN 接口扫描：枚举系统可用 CAN 后端与 USB-CAN 设备"""

import argparse
import json
import sys
import platform
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"
COMMON_INTERFACES_PATH = Path(__file__).parent.parent / "references" / "common_interfaces.json"


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_known_devices():
    try:
        data = json.loads(COMMON_INTERFACES_PATH.read_text(encoding="utf-8"))
        return data.get("usb_can_devices", []), data.get("known_interfaces", [])
    except Exception:
        return [], []


def check_interface_available(interface_name):
    """尝试导入对应后端，判断是否可用"""
    try:
        import can
        # 利用 python-can 的接口枚举检测后端是否注册
        from can.interfaces import VALID_INTERFACES
        return interface_name in VALID_INTERFACES
    except Exception:
        return False


def scan_usb_devices():
    """扫描 USB 设备，匹配已知 USB-CAN 适配器"""
    known_devices, _ = load_known_devices()
    if not known_devices:
        return []

    found = []
    is_windows = platform.system() == "Windows"

    if is_windows:
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class USB -Status OK | Select-Object -Property InstanceId,FriendlyName | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                devices = json.loads(result.stdout)
                if isinstance(devices, dict):
                    devices = [devices]
                for dev in devices:
                    instance_id = dev.get("InstanceId", "").upper()
                    friendly = dev.get("FriendlyName", "")
                    for known in known_devices:
                        vid = known["vid"].upper()
                        pid = known["pid"].upper()
                        if f"VID_{vid}" in instance_id and f"PID_{pid}" in instance_id:
                            found.append({
                                "name": known["name"],
                                "vid": vid,
                                "pid": pid,
                                "interface": known["interface"],
                                "channel": known["channel"],
                                "friendly_name": friendly,
                            })
        except Exception:
            pass
    else:
        # Linux: 检查 /sys/bus/usb/devices
        try:
            import subprocess
            result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line_upper = line.upper()
                    for known in known_devices:
                        vid = known["vid"].upper()
                        pid = known["pid"].upper()
                        if f"{vid}:{pid}" in line_upper:
                            found.append({
                                "name": known["name"],
                                "vid": vid,
                                "pid": pid,
                                "interface": known["interface"],
                                "channel": known["channel"],
                                "friendly_name": line.strip(),
                            })
        except Exception:
            pass

    return found


def scan_socketcan():
    """Linux: 扫描 SocketCAN 接口"""
    if platform.system() != "Linux":
        return []
    interfaces = []
    try:
        import subprocess
        result = subprocess.run(
            ["ip", "-j", "link", "show", "type", "can"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for iface in json.loads(result.stdout):
                interfaces.append({
                    "interface": "socketcan",
                    "channel": iface.get("ifname", ""),
                    "status": iface.get("operstate", "unknown").lower(),
                })
    except Exception:
        pass
    return interfaces


def scan_interfaces():
    """综合扫描所有可用 CAN 接口"""
    try:
        import can  # noqa: F401
    except ImportError:
        return None, "python-can 未安装，请执行 pip install python-can"

    results = []

    # 1. 扫描 USB-CAN 设备
    usb_devices = scan_usb_devices()
    for dev in usb_devices:
        results.append({
            "interface": dev["interface"],
            "channel": dev["channel"],
            "device": dev["name"],
            "vid": dev["vid"],
            "pid": dev["pid"],
            "status": "detected",
        })

    # 2. 扫描 SocketCAN（Linux）
    for iface in scan_socketcan():
        results.append({
            "interface": iface["interface"],
            "channel": iface["channel"],
            "device": "",
            "vid": "",
            "pid": "",
            "status": iface["status"],
        })

    # 3. 检查已知后端可用性
    _, known_interfaces = load_known_devices()
    backends_found = {r["interface"] for r in results}
    for ki in known_interfaces:
        iface_name = ki["interface"]
        if iface_name not in backends_found:
            if check_interface_available(iface_name):
                results.append({
                    "interface": iface_name,
                    "channel": "",
                    "device": "",
                    "vid": "",
                    "pid": "",
                    "status": "backend_available",
                })

    return results, None


def output_json(result):
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description="扫描可用 CAN 接口")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    interfaces, err = scan_interfaces()

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
        "details": {"interfaces": interfaces},
    }

    if args.json:
        output_json(result)
    else:
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
