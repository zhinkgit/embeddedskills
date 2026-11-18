from __future__ import annotations

import base64
import codecs
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from terminal_runtime import TerminalError, failure, read_mapping, result
from terminal_transport import ProcessTransport, SerialTransport, background_options


MAX_BUFFER = 1024 * 1024
MAX_MESSAGE = 8 * MAX_BUFFER


def state_file(workspace: Path) -> Path:
    return workspace / ".embeddedskills" / "state" / "terminal.json"


def write_json(path: Path, data: dict):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False)
        except BaseException:
            stream.close()
            temporary.unlink()
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked_state(workspace: Path):
    path = state_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield path


class Session:
    def __init__(self, spec: dict, log):
        self.spec = spec
        self.log = log
        self.buffer = bytearray()
        self.dropped = 0
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.closed = False
        factory = SerialTransport if spec["backend"] == "serial" else ProcessTransport
        self.transport = factory(spec, self.receive)

    def receive(self, data: bytes):
        with self.condition:
            if self.log and not self.log.closed:
                self.log.write(data)
                self.log.flush()
            self.buffer.extend(data)
            overflow = max(0, len(self.buffer) - MAX_BUFFER)
            if overflow:
                del self.buffer[:overflow]
                self.dropped += overflow
            self.condition.notify_all()

    def close(self):
        with self.close_lock:
            if not self.closed:
                self.closed = True
                with self.condition:
                    self.condition.notify_all()
                try:
                    self.transport.close()
                finally:
                    with self.condition:
                        if self.log:
                            self.log.close()

    def handle(self, request: dict) -> dict:
        action = request["action"]
        name = self.spec["name"]
        if action == "status":
            return result(action, f"会话状态：{name}", name=name, backend=self.spec["backend"],
                          log_file=self.spec.get("log_file"), **self.transport.status())
        if action == "stop":
            self.close()
            return result(action, f"已停止会话：{name}", name=name)
        if action == "send":
            data = base64.b64decode(request["data"], validate=True)
            with self.write_lock:
                if self.closed:
                    raise TerminalError("session_closed", f"会话已关闭：{name}")
                self.transport.write(data)
            return result(action, f"已写入 {len(data)} 字节", name=name, bytes=len(data))
        if action != "read":
            raise TerminalError("invalid_action", f"未知会话操作：{action}")
        timeout = request["timeout"]
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.buffer and not self.closed and self.transport.status()["alive"]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, 0.1))
            data = bytes(self.buffer)
            self.buffer.clear()
            dropped, self.dropped = self.dropped, 0
        alive = not self.closed and self.transport.status()["alive"]
        value = {"hex": data.hex(" ")} if request.get("hex") else {
            "text": data.decode(request["encoding"], errors="replace")
        }
        return result(action, f"已读取 {len(data)} 字节", name=name, bytes=len(data),
                      alive=alive, timed_out=timeout > 0 and alive and not data,
                      dropped_bytes=dropped, **value)


def receive_json(conn: socket.socket) -> dict:
    with conn.makefile("rb") as stream:
        line = stream.readline(MAX_MESSAGE + 1)
    if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
        raise ValueError("无效的会话消息长度")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("无效的会话消息")
    return value


def send_json(conn: socket.socket, payload: dict):
    conn.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")


def handle_connection(conn, session, token, stopped):
    action = "request"
    with conn:
        conn.settimeout(2)
        try:
            request = receive_json(conn)
            supplied = request.get("token")
            if not isinstance(supplied, str) or not supplied.isascii() or not secrets.compare_digest(supplied, token):
                return
            action = request["action"]
            response = session.handle(request)
        except (OSError, ValueError, LookupError) as exc:
            response = failure(action, getattr(exc, "code", "session_io_failed"), str(exc))
        try:
            send_json(conn, response)
        except OSError:
            pass
        finally:
            if action == "stop" and session.closed:
                stopped.set()


def serve(start_file: Path) -> int:
    spec = read_mapping(start_file)
    action = "start"
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(0.25)
            log_context = open(spec["log_file"], "ab") if spec.get("log_file") else nullcontext()
            with log_context as log:
                session = Session(spec, log)
                try:
                    if spec["backend"] == "ssh":
                        session.transport.wait_ready(spec["startup_timeout"])
                    elif spec["backend"] == "tunnel":
                        deadline = time.monotonic() + spec["tunnel_timeout"]
                        while not can_connect(spec["local_port"]):
                            if not session.transport.status()["alive"] or time.monotonic() >= deadline:
                                diagnostic = bytes(session.buffer).decode("utf-8", errors="replace").strip()
                                raise TerminalError("tunnel_start_failed", diagnostic or "SSH 转发未就绪")
                            time.sleep(0.05)
                    elif not session.transport.status()["alive"]:
                        raise TerminalError("start_failed", "会话进程已退出")
                    write_json(start_file, {"port": listener.getsockname()[1]})
                    stopped = threading.Event()
                    dead_since = None
                    while not stopped.is_set():
                        if session.transport.status()["alive"]:
                            dead_since = None
                        else:
                            dead_since = dead_since or time.monotonic()
                            if time.monotonic() - dead_since >= 60:
                                break
                        try:
                            conn, _ = listener.accept()
                        except TimeoutError:
                            continue
                        threading.Thread(target=handle_connection,
                                         args=(conn, session, spec["token"], stopped), daemon=True).start()
                finally:
                    session.close()
    except (TerminalError, OSError, ValueError) as exc:
        if start_file.exists():
            write_json(start_file, failure(action, getattr(exc, "code", "start_failed"), str(exc)))
        return 1
    return 0


def can_connect(port: int) -> bool:
    with socket.socket() as conn:
        conn.settimeout(0.1)
        return conn.connect_ex(("127.0.0.1", port)) == 0


def call(record: dict, action: str, **payload) -> dict:
    try:
        with socket.create_connection(("127.0.0.1", record["port"]), timeout=3) as conn:
            conn.settimeout(payload.get("timeout", 0) + 5)
            send_json(conn, {"action": action, "token": record["token"], **payload})
            return receive_json(conn)
    except TimeoutError:
        return failure(action, "session_timeout", "终端后台进程没有及时响应")
    except (OSError, ValueError):
        return failure(action, "session_unreachable", "会话不可达，后台进程可能已退出")


def start(workspace: Path, spec: dict) -> dict:
    name = spec["name"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise TerminalError("invalid_session_name", "会话名应为 1–64 位字母、数字、点、下划线或连字符")
    with locked_state(workspace) as path:
        records = read_mapping(path)
        existing = records.get(name)
        if existing:
            check = call(existing, "status")
            if check.get("error", {}).get("code") != "session_unreachable":
                raise TerminalError("session_exists", f"会话已存在：{name}")
        if spec.get("log_file"):
            Path(spec["log_file"]).parent.mkdir(parents=True, exist_ok=True)
        if spec["backend"] == "tunnel" and can_connect(spec["local_port"]):
            raise TerminalError("port_in_use", f"本地端口已占用：{spec['local_port']}")
        token = secrets.token_hex(32)
        pending = path.parent / f".terminal-{token[:16]}.json"
        write_json(pending, {**spec, "token": token})
        worker = None
        record = None
        registered = False
        try:
            worker = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), str(pending)],
                cwd=Path(__file__).resolve().parent,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **background_options(),
            )
            deadline = time.monotonic() + spec.get("tunnel_timeout", spec.get("startup_timeout", 3)) + 3
            while time.monotonic() < deadline:
                handshake = read_mapping(pending)
                if handshake.get("status") == "error":
                    raise TerminalError(handshake["error"]["code"], handshake["summary"])
                if "port" in handshake:
                    record = {"port": handshake["port"], "token": token,
                              "encoding": spec["encoding"], "backend": spec["backend"]}
                    records[name] = record
                    write_json(path, records)
                    registered = True
                    return result("start", f"已启动会话：{name}", name=name,
                                  backend=spec["backend"], log_file=spec.get("log_file"))
                if worker.poll() is not None:
                    break
                time.sleep(0.025)
            raise TerminalError("start_failed", f"后台会话未就绪：{name}")
        finally:
            pending.unlink(missing_ok=True)
            if worker is not None and not registered:
                if record:
                    call(record, "stop")
                try:
                    worker.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    worker.terminate()
                    worker.wait(timeout=3)


def resolve_record(workspace: Path, name: str, backend: str | None = None) -> dict:
    record = read_mapping(state_file(workspace)).get(name)
    if not record or (backend and record["backend"] != backend):
        raise TerminalError("session_not_found", f"未找到会话：{name}")
    return record


def session_list(workspace: Path) -> dict:
    sessions = []
    for name, record in read_mapping(state_file(workspace)).items():
        response = call(record, "status")
        sessions.append({"name": name, "backend": record["backend"], "alive": False, **response["details"]})
    return result("list", f"发现 {len(sessions)} 个会话记录", sessions=sessions)


def stop(workspace: Path, name: str, backend: str | None = None) -> dict:
    with locked_state(workspace) as path:
        record = resolve_record(workspace, name, backend)
        response = call(record, "stop")
        if response["status"] == "ok" or response["error"]["code"] == "session_unreachable":
            records = read_mapping(path)
            del records[name]
            write_json(path, records)
        return response


def encoding_for(name: str) -> str:
    return codecs.lookup(name).name


if __name__ == "__main__":
    raise SystemExit(serve(Path(sys.argv[1])))
