"""CAN 数据库解码：用 DBC/ARXML/KCD 等文件解码单帧或日志"""

import argparse
import json
import sys
from pathlib import Path


def parse_hex_data(s):
    s = s.replace(" ", "").replace(",", "")
    return bytes.fromhex(s)


def output_json(result):
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def load_database(db_path, db_format="auto"):
    """加载数据库文件"""
    import cantools

    path = Path(db_path)
    if not path.exists():
        return None, f"数据库文件不存在: {db_path}"

    try:
        if db_format == "auto":
            db = cantools.database.load_file(str(path))
        else:
            db = cantools.database.Database()
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            format_map = {
                "dbc": "dbc",
                "arxml": "autosar",
                "kcd": "kcd",
                "sym": "sym",
                "cdd": "cdd",
            }
            fmt = format_map.get(db_format)
            if fmt:
                db.add_dbc_string(content) if fmt == "dbc" else db.add_autosar_string(content) if fmt == "autosar" else None
                # 对于非 dbc/autosar，回退到 load_file
                if fmt not in ("dbc", "autosar"):
                    db = cantools.database.load_file(str(path))
            else:
                return None, f"不支持的数据库格式: {db_format}"
        return db, None
    except Exception as e:
        return None, f"加载数据库失败: {e}"


def list_messages(db, signal_filter=None):
    """列出数据库中的报文定义"""
    messages = []
    for msg in db.messages:
        signals = []
        for sig in msg.signals:
            if signal_filter and signal_filter.lower() not in sig.name.lower():
                continue
            sig_info = {
                "name": sig.name,
                "start_bit": sig.start,
                "length": sig.length,
                "unit": sig.unit or "",
                "min": sig.minimum,
                "max": sig.maximum,
            }
            signals.append(sig_info)

        if signal_filter and not signals:
            continue

        messages.append({
            "name": msg.name,
            "id": f"0x{msg.frame_id:03X}",
            "dlc": msg.length,
            "signals": signals,
        })
    return messages


def decode_single(db, arb_id, data):
    """解码单帧"""
    try:
        msg = db.get_message_by_frame_id(arb_id)
    except KeyError:
        return None, f"数据库中未找到 ID 0x{arb_id:03X} 的定义"

    try:
        decoded = msg.decode(data)
        signals = []
        for k, v in decoded.items():
            sig_info = {"name": k, "value": round(v, 6) if isinstance(v, float) else v}
            # 查找信号的单位
            for sig in msg.signals:
                if sig.name == k:
                    sig_info["unit"] = sig.unit or ""
                    break
            signals.append(sig_info)
        return {"message": msg.name, "id": f"0x{arb_id:03X}", "signals": signals}, None
    except Exception as e:
        return None, f"解码失败: {e}"


def decode_log_file(db, log_path, signal_filter=None):
    """解码日志文件"""
    import can

    path = Path(log_path)
    if not path.exists():
        return None, f"日志文件不存在: {log_path}"

    results = []
    errors = 0

    try:
        reader = can.LogReader(str(path))
        for msg in reader:
            try:
                db_msg = db.get_message_by_frame_id(msg.arbitration_id)
                decoded = db_msg.decode(msg.data)
                if signal_filter:
                    decoded = {k: v for k, v in decoded.items() if signal_filter.lower() in k.lower()}
                    if not decoded:
                        continue
                decoded = {k: round(v, 6) if isinstance(v, float) else v for k, v in decoded.items()}
                results.append({
                    "timestamp": round(msg.timestamp, 6),
                    "message": db_msg.name,
                    "id": f"0x{msg.arbitration_id:03X}",
                    "decoded": decoded,
                })
            except (KeyError, Exception):
                errors += 1
                continue
    except Exception as e:
        return None, f"读取日志失败: {e}"

    return {"frames": results, "decoded_count": len(results), "error_count": errors}, None


def main():
    parser = argparse.ArgumentParser(description="CAN 数据库解码")
    parser.add_argument("db_file", help="数据库文件路径（DBC/ARXML/KCD/SYM/CDD）")
    parser.add_argument("--db-format", default="auto", help="数据库格式（auto|dbc|arxml|kcd|sym|cdd）")
    parser.add_argument("--id", help="单帧 CAN ID（支持 0x 前缀）")
    parser.add_argument("--data", help="单帧数据（Hex）")
    parser.add_argument("--log", help="日志文件路径")
    parser.add_argument("--signal", help="按信号名过滤")
    parser.add_argument("--list", action="store_true", help="列出数据库中所有报文定义")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    try:
        import cantools  # noqa: F401
    except ImportError:
        err = {"status": "error", "action": "decode", "error": {"code": "import_error", "message": "cantools 未安装，请执行 pip install cantools"}}
        if args.json:
            output_json(err)
        else:
            print(f"错误: {err['error']['message']}", file=sys.stderr)
        sys.exit(1)

    db, err = load_database(args.db_file, args.db_format)
    if err:
        result = {"status": "error", "action": "decode", "error": {"code": "db_load_failed", "message": err}}
        if args.json:
            output_json(result)
        else:
            print(f"错误: {err}", file=sys.stderr)
        sys.exit(1)

    # 列出报文定义
    if args.list:
        messages = list_messages(db, args.signal)
        result = {
            "status": "ok",
            "action": "decode",
            "summary": f"数据库包含 {len(messages)} 个报文",
            "details": {"messages": messages},
        }
        if args.json:
            output_json(result)
        else:
            for m in messages:
                print(f"\n{m['name']} ({m['id']}) DLC={m['dlc']}")
                for s in m["signals"]:
                    unit = f" [{s['unit']}]" if s["unit"] else ""
                    print(f"  {s['name']}: bit {s['start_bit']}+{s['length']}{unit} ({s['min']}~{s['max']})")
        return

    # 解码单帧
    if args.id and args.data:
        arb_id = int(args.id, 0)
        data = parse_hex_data(args.data)
        decoded, err = decode_single(db, arb_id, data)
        if err:
            result = {"status": "error", "action": "decode", "error": {"code": "decode_failed", "message": err}}
            if args.json:
                output_json(result)
            else:
                print(f"错误: {err}", file=sys.stderr)
            sys.exit(1)

        result = {
            "status": "ok",
            "action": "decode",
            "summary": f"已解码 {decoded['message']} ({decoded['id']})",
            "details": decoded,
        }
        if args.json:
            output_json(result)
        else:
            print(f"\n{decoded['message']} ({decoded['id']})")
            for s in decoded["signals"]:
                unit = f" {s.get('unit', '')}" if s.get("unit") else ""
                print(f"  {s['name']} = {s['value']}{unit}")
        return

    # 解码日志
    if args.log:
        log_result, err = decode_log_file(db, args.log, args.signal)
        if err:
            result = {"status": "error", "action": "decode", "error": {"code": "log_decode_failed", "message": err}}
            if args.json:
                output_json(result)
            else:
                print(f"错误: {err}", file=sys.stderr)
            sys.exit(1)

        result = {
            "status": "ok",
            "action": "decode",
            "summary": f"已解码 {log_result['decoded_count']} 帧（{log_result['error_count']} 帧无法解码）",
            "details": log_result,
        }
        if args.json:
            output_json(result)
        else:
            for f in log_result["frames"]:
                sigs = ", ".join(f"{k}={v}" for k, v in f["decoded"].items())
                print(f"[{f['timestamp']:.6f}] {f['message']} ({f['id']}): {sigs}")
            print(f"\n解码 {log_result['decoded_count']} 帧, {log_result['error_count']} 帧无法解码")
        return

    # 未指定操作时提示
    print("请指定操作：--list 列出定义、--id + --data 解码单帧、--log 解码日志", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
