from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

from terminal_runtime import TerminalError


def background_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class ProcessTransport:
    def __init__(self, spec: dict, receive):
        self.ready = threading.Event()
        self.diagnostics = bytearray()
        ssh = spec["backend"] == "ssh"
        self.process = subprocess.Popen(
            spec["argv"], cwd=spec.get("cwd"), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE if ssh else subprocess.STDOUT, bufsize=0,
            **background_options(),
        )
        self.reader = threading.Thread(target=self._read, args=(receive,), daemon=True)
        self.reader.start()
        self.diagnostic_reader = None
        if ssh:
            self.diagnostic_reader = threading.Thread(target=self._read_ssh_diagnostics, args=(receive,), daemon=True)
            self.diagnostic_reader.start()
        else:
            self.ready.set()

    def _read(self, receive):
        with self.process.stdout as output:
            while chunk := output.read(4096):
                receive(chunk)

    def _read_ssh_diagnostics(self, receive):
        with self.process.stderr as output:
            for line in output:
                if line.startswith(b"debug2: shell request accepted on channel "):
                    self.ready.set()
                if not line.startswith((b"debug1:", b"debug2:", b"debug3:")):
                    self.diagnostics.extend(line)
                    del self.diagnostics[:-4096]
                    receive(line)

    def wait_ready(self, timeout: float):
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            if self.ready.wait(0.1):
                return
        if self.diagnostic_reader and self.process.poll() is not None:
            self.diagnostic_reader.join(timeout=0.5)
        diagnostic = bytes(self.diagnostics).decode("utf-8", errors="replace").strip()
        raise TerminalError("start_failed", diagnostic or "SSH 会话未就绪或已退出")

    def write(self, data: bytes):
        with memoryview(data) as payload:
            offset = 0
            while offset < len(payload):
                offset += self.process.stdin.write(payload[offset:])

    def status(self) -> dict:
        code = self.process.poll()
        return {"alive": code is None, "exit_code": code}

    def close(self):
        if self.process.poll() is None:
            try:
                if os.name == "nt":
                    self.process.terminate()
                else:
                    os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except ProcessLookupError:
                self.process.wait()
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.process.stdin.close()
        self.reader.join(timeout=1)
        if self.diagnostic_reader:
            self.diagnostic_reader.join(timeout=1)


def serial_module():
    try:
        import serial
        from serial.tools import list_ports
    except ImportError as exc:
        raise TerminalError("missing_dependency", "串口功能需要 pyserial") from exc
    return serial, list_ports


def scan_serial() -> list[dict]:
    _, list_ports = serial_module()
    return [
        {"port": p.device, "description": p.description, "hwid": p.hwid,
         "vid": p.vid, "pid": p.pid, "serial_number": p.serial_number}
        for p in sorted(list_ports.comports())
    ]


class SerialTransport:
    def __init__(self, spec: dict, receive):
        serial, _ = serial_module()
        self.closed = threading.Event()
        self.read_error = None
        try:
            options = {"exclusive": True} if os.name != "nt" else {}
            self.port = serial.Serial(
                port=spec["port"], baudrate=spec["baudrate"], bytesize=spec["bytesize"],
                parity=spec["parity"], stopbits=spec["stopbits"], timeout=0.1,
                write_timeout=2, **options,
            )
        except (OSError, ValueError) as exc:
            raise TerminalError("serial_open_failed", f"无法打开串口 {spec['port']}：{exc}") from exc
        self.reader = threading.Thread(target=self._read, args=(receive,), daemon=True)
        self.reader.start()

    def _read(self, receive):
        while not self.closed.is_set():
            try:
                chunk = self.port.read(4096)
            except OSError as exc:
                if not self.closed.is_set():
                    self.read_error = str(exc)
                break
            if chunk:
                receive(chunk)

    def write(self, data: bytes):
        self.port.write(data)

    def status(self) -> dict:
        return {"alive": self.port.is_open and self.read_error is None, "read_error": self.read_error}

    def close(self):
        self.closed.set()
        self.port.close()
        self.reader.join(timeout=1)
