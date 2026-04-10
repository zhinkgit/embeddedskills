"""CAN 总线统计：负载率、ID 分布、帧率和数据变化"""

import argparse
import json
import sys
import time
from collections import defaultdict
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
    parser = argparse.ArgumentParser(description="CAN 总线统计")
    parser.add_argument("--duration", type=float, default=5.0, help="统计时长（秒，默认 5）")
    parser.add_argument("--top", type=int, default=20, help="显示前 N 个 ID")
    parser.add_argument("--watch", help="重点观察的 ID 列表（逗号分隔）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    try:
        import can
    except ImportError:
        err = {"status": "error", "action": "stats", "error": {"code": "import_error", "message": "python-can 未安装，请执行 pip install python-can"}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    interface = config.get("default_interface", "")
    channel = config.get("default_channel", "")
    bitrate = config.get("default_bitrate", 0)

    if not interface or not channel:
        err = {"status": "error", "action": "stats", "error": {"code": "config_missing", "message": "config.json 缺少 default_interface 或 default_channel"}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    watch_ids = parse_id_list(args.watch)

    bus_kwargs = {"interface": interface, "channel": channel}
    if bitrate:
        bus_kwargs["bitrate"] = bitrate

    try:
        bus = can.Bus(**bus_kwargs)
    except Exception as e:
        err = {"status": "error", "action": "stats", "error": {"code": "interface_open_failed", "message": str(e)}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: 无法打开 CAN 接口 — {e}", file=sys.stderr)
        sys.exit(1)

    # 统计数据
    id_count = defaultdict(int)
    id_bytes = defaultdict(int)
    id_last_data = {}
    id_data_changes = defaultdict(int)
    total_bits = 0

    start = time.time()
    total_frames = 0

    print(f"统计中（{args.duration} 秒）...", file=sys.stderr)

    try:
        while True:
            elapsed = time.time() - start
            if elapsed >= args.duration:
                break

            remaining = args.duration - elapsed
            msg = bus.recv(timeout=min(remaining, 0.5))
            if msg is None:
                continue

            total_frames += 1
            arb_id = msg.arbitration_id
            id_count[arb_id] += 1
            id_bytes[arb_id] += msg.dlc

            # 估算总线比特数: SOF(1) + ID(11/29) + 控制(6) + 数据(dlc*8) + CRC(15) + ACK(2) + EOF(7) + IFS(3)
            if msg.is_extended_id:
                frame_bits = 1 + 29 + 6 + msg.dlc * 8 + 15 + 2 + 7 + 3
            else:
                frame_bits = 1 + 11 + 6 + msg.dlc * 8 + 15 + 2 + 7 + 3
            total_bits += frame_bits

            # 数据变化检测
            data_hex = msg.data.hex()
            if arb_id in id_last_data and id_last_data[arb_id] != data_hex:
                id_data_changes[arb_id] += 1
            id_last_data[arb_id] = data_hex

    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()

    actual_duration = time.time() - start
    bus_bitrate = bitrate if bitrate else 500000  # 默认估算用 500k
    bus_load = (total_bits / (actual_duration * bus_bitrate)) * 100 if actual_duration > 0 else 0

    # 排序 ID 列表
    sorted_ids = sorted(id_count.keys(), key=lambda x: id_count[x], reverse=True)

    ids_detail = []
    for arb_id in sorted_ids[:args.top]:
        entry = {
            "id": f"0x{arb_id:03X}",
            "count": id_count[arb_id],
            "rate_hz": round(id_count[arb_id] / actual_duration, 1) if actual_duration > 0 else 0,
            "total_bytes": id_bytes[arb_id],
            "data_changes": id_data_changes.get(arb_id, 0),
            "last_data": format_data(bytes.fromhex(id_last_data.get(arb_id, ""))),
        }
        ids_detail.append(entry)

    # watch ID 额外输出
    watch_detail = []
    if watch_ids:
        for arb_id in sorted(watch_ids):
            if arb_id in id_count:
                watch_detail.append({
                    "id": f"0x{arb_id:03X}",
                    "count": id_count[arb_id],
                    "rate_hz": round(id_count[arb_id] / actual_duration, 1) if actual_duration > 0 else 0,
                    "data_changes": id_data_changes.get(arb_id, 0),
                    "last_data": format_data(bytes.fromhex(id_last_data.get(arb_id, ""))),
                })
            else:
                watch_detail.append({
                    "id": f"0x{arb_id:03X}",
                    "count": 0,
                    "rate_hz": 0,
                    "data_changes": 0,
                    "last_data": "",
                })

    result = {
        "status": "ok",
        "action": "stats",
        "summary": f"{actual_duration:.1f} 秒内收到 {total_frames} 帧，{len(id_count)} 个不同 ID，总线负载约 {bus_load:.1f}%（估算值）",
        "details": {
            "duration_sec": round(actual_duration, 1),
            "total_frames": total_frames,
            "unique_ids": len(id_count),
            "bus_load_percent": round(bus_load, 1),
            "bus_load_note": "估算值，基于标准帧位数计算",
            "ids": ids_detail,
        },
    }
    if watch_detail:
        result["details"]["watched"] = watch_detail

    if args.json:
        output_json(result)
    else:
        print(f"\n统计结果 ({actual_duration:.1f} 秒):")
        print(f"  总帧数: {total_frames}")
        print(f"  不同 ID: {len(id_count)}")
        print(f"  总线负载: ~{bus_load:.1f}%（估算）")
        print(f"\n  {'ID':<12} {'帧数':>8} {'帧率(Hz)':>10} {'数据变化':>8} {'最新数据'}")
        print(f"  {'─'*12} {'─'*8} {'─'*10} {'─'*8} {'─'*24}")
        for entry in ids_detail:
            print(f"  {entry['id']:<12} {entry['count']:>8} {entry['rate_hz']:>10.1f} {entry['data_changes']:>8} {entry['last_data']}")

        if watch_detail:
            print(f"\n  观察 ID:")
            for entry in watch_detail:
                print(f"  {entry['id']}: {entry['count']} 帧, {entry['rate_hz']:.1f} Hz, {entry['data_changes']} 次变化, 最新: {entry['last_data']}")


if __name__ == "__main__":
    main()
