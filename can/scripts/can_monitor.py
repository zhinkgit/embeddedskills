"""CAN 总线监控：持续读取报文，支持过滤、DBC 解码和 CAN-FD"""

import argparse
import json
import sys
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_id_list(s):
    """解析逗号分隔的 CAN ID 列表，支持 0x 前缀"""
    if not s:
        return None
    ids = set()
    for part in s.split(","):
        part = part.strip()
        if part:
            ids.add(int(part, 0))
    return ids


def output_json_line(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def format_data(data):
    return " ".join(f"{b:02X}" for b in data)


def main():
    parser = argparse.ArgumentParser(description="CAN 总线实时监控")
    parser.add_argument("--fd", action="store_true", help="启用 CAN-FD 模式")
    parser.add_argument("--filter-id", help="只显示指定 ID（逗号分隔）")
    parser.add_argument("--exclude-id", help="排除指定 ID（逗号分隔）")
    parser.add_argument("--dbc", help="DBC 数据库文件路径，用于解码")
    parser.add_argument("--timeout", type=float, help="监控时长（秒）")
    parser.add_argument("--json", action="store_true", help="JSON Lines 输出")
    args = parser.parse_args()

    try:
        import can
    except ImportError:
        err = {"status": "error", "action": "monitor", "error": {"code": "import_error", "message": "python-can 未安装，请执行 pip install python-can"}}
        if args.json:
            output_json_line(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    interface = config.get("default_interface", "")
    channel = config.get("default_channel", "")
    bitrate = config.get("default_bitrate", 0)
    data_bitrate = config.get("default_data_bitrate", 0)

    if not interface or not channel:
        err = {"status": "error", "action": "monitor", "error": {"code": "config_missing", "message": "config.json 缺少 default_interface 或 default_channel"}}
        if args.json:
            output_json_line(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    filter_ids = parse_id_list(args.filter_id)
    exclude_ids = parse_id_list(args.exclude_id)

    # 加载 DBC
    db = None
    if args.dbc:
        try:
            import cantools
            db = cantools.database.load_file(args.dbc)
        except ImportError:
            print("警告: cantools 未安装，DBC 解码不可用", file=sys.stderr)
        except Exception as e:
            print(f"警告: 加载 DBC 失败: {e}", file=sys.stderr)

    # 连接总线
    bus_kwargs = {"interface": interface, "channel": channel}
    if bitrate:
        bus_kwargs["bitrate"] = bitrate
    if args.fd and data_bitrate:
        bus_kwargs["fd"] = True
        bus_kwargs["data_bitrate"] = data_bitrate

    try:
        bus = can.Bus(**bus_kwargs)
    except Exception as e:
        err = {"status": "error", "action": "monitor", "error": {"code": "interface_open_failed", "message": str(e)}}
        if args.json:
            output_json_line(err)
        else:
            print(f"错误: 无法打开 CAN 接口 — {e}", file=sys.stderr)
        sys.exit(1)

    start = time.time()
    count = 0

    try:
        while True:
            if args.timeout and (time.time() - start) >= args.timeout:
                break

            remaining = None
            if args.timeout:
                remaining = args.timeout - (time.time() - start)
                if remaining <= 0:
                    break

            msg = bus.recv(timeout=min(remaining, 1.0) if remaining else 1.0)
            if msg is None:
                continue

            arb_id = msg.arbitration_id
            if filter_ids and arb_id not in filter_ids:
                continue
            if exclude_ids and arb_id in exclude_ids:
                continue

            count += 1

            # 尝试 DBC 解码
            decoded = None
            if db:
                try:
                    db_msg = db.get_message_by_frame_id(arb_id)
                    decoded = db_msg.decode(msg.data)
                    decoded = {k: round(v, 6) if isinstance(v, float) else v for k, v in decoded.items()}
                except Exception:
                    pass

            if args.json:
                obj = {
                    "timestamp": round(msg.timestamp, 6),
                    "id": f"0x{arb_id:03X}",
                    "dlc": msg.dlc,
                    "data": format_data(msg.data),
                    "is_fd": msg.is_fd,
                }
                if decoded:
                    obj["decoded"] = decoded
                output_json_line(obj)
            else:
                fd_flag = " [FD]" if msg.is_fd else ""
                dec_str = ""
                if decoded:
                    dec_str = " | " + ", ".join(f"{k}={v}" for k, v in decoded.items())
                print(f"[{msg.timestamp:.6f}] 0x{arb_id:03X} [{msg.dlc}] {format_data(msg.data)}{fd_flag}{dec_str}")

    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()
        elapsed = time.time() - start
        print(f"\n监控结束: {count} 帧, {elapsed:.1f} 秒", file=sys.stderr)


if __name__ == "__main__":
    main()
