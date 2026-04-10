"""CAN 总线日志记录：保存报文到 ASC / BLF / CSV / LOG 文件"""

import argparse
import json
import os
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
    if not s:
        return None
    ids = set()
    for part in s.split(","):
        part = part.strip()
        if part:
            ids.add(int(part, 0))
    return ids


def format_data(data):
    return " ".join(f"{b:02X}" for b in data)


def output_json(result):
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description="CAN 总线日志记录")
    parser.add_argument("--output", "-o", help="输出文件路径（按扩展名选格式：.asc/.blf/.csv/.log）")
    parser.add_argument("--duration", type=float, help="记录时长（秒）")
    parser.add_argument("--max-count", type=int, help="最大记录帧数")
    parser.add_argument("--filter-id", help="只记录指定 ID（逗号分隔）")
    parser.add_argument("--console", action="store_true", help="同时输出到控制台")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    try:
        import can
    except ImportError:
        err = {"status": "error", "action": "log", "error": {"code": "import_error", "message": "python-can 未安装，请执行 pip install python-can"}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    interface = config.get("default_interface", "")
    channel = config.get("default_channel", "")
    bitrate = config.get("default_bitrate", 0)
    log_dir = config.get("default_log_dir", ".logs")

    if not interface or not channel:
        err = {"status": "error", "action": "log", "error": {"code": "config_missing", "message": "config.json 缺少 default_interface 或 default_channel"}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    # 确定输出文件
    if args.output:
        output_path = Path(args.output)
    else:
        os.makedirs(log_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_path = Path(log_dir) / f"can_{timestamp}.asc"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    filter_ids = parse_id_list(args.filter_id)

    bus_kwargs = {"interface": interface, "channel": channel}
    if bitrate:
        bus_kwargs["bitrate"] = bitrate

    try:
        bus = can.Bus(**bus_kwargs)
    except Exception as e:
        err = {"status": "error", "action": "log", "error": {"code": "interface_open_failed", "message": str(e)}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: 无法打开 CAN 接口 — {e}", file=sys.stderr)
        sys.exit(1)

    # 根据扩展名选择 logger
    try:
        logger = can.Logger(str(output_path))
    except Exception as e:
        bus.shutdown()
        err = {"status": "error", "action": "log", "error": {"code": "logger_init_failed", "message": str(e)}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: 无法创建日志文件 — {e}", file=sys.stderr)
        sys.exit(1)

    start = time.time()
    count = 0

    print(f"开始记录到 {output_path} ...", file=sys.stderr)

    try:
        while True:
            if args.duration and (time.time() - start) >= args.duration:
                break
            if args.max_count and count >= args.max_count:
                break

            remaining = None
            if args.duration:
                remaining = args.duration - (time.time() - start)
                if remaining <= 0:
                    break

            msg = bus.recv(timeout=min(remaining, 1.0) if remaining else 1.0)
            if msg is None:
                continue

            if filter_ids and msg.arbitration_id not in filter_ids:
                continue

            logger.on_message_received(msg)
            count += 1

            if args.console:
                print(f"[{msg.timestamp:.6f}] 0x{msg.arbitration_id:03X} [{msg.dlc}] {format_data(msg.data)}", file=sys.stderr)

    except KeyboardInterrupt:
        pass
    finally:
        logger.stop()
        bus.shutdown()

    elapsed = time.time() - start
    result = {
        "status": "ok",
        "action": "log",
        "summary": f"记录 {count} 帧到 {output_path}",
        "details": {
            "file": str(output_path),
            "frames": count,
            "duration_sec": round(elapsed, 1),
        },
    }

    if args.json:
        output_json(result)
    else:
        print(f"\n记录完成: {count} 帧, {elapsed:.1f} 秒 -> {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
