import asyncio
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_release import build_archives


ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINTS = {
    "can": ("can_decode.py", ["--json"]),
    "eide": ("eide_build.py", ["build", "--builder-exe", "skill-test-missing", "--json"]),
    "gcc": ("gcc_size.py", ["analyze", "--elf", "missing.elf", "--json"]),
    "jlink": ("jlink_exec.py", ["flash", "--json"]),
    "keil": ("keil_build.py", ["build", "--uv4", "skill-test-missing", "--json"]),
    "net": ("net_ping.py", ["--json"]),
    "openocd": ("openocd_run.py", ["flash", "--json"]),
    "probe-rs": ("probe_rs_exec.py", ["list", "--exe", "skill-test-missing", "--json"]),
    "serial": ("serial_send.py", ["--json"]),
    "terminal": ("terminal.py", ["--json", "exec", "local", "--", "skill-test-missing"]),
    "workflow": ("workflow.py", ["plan", "--json", "--workspace", "missing"]),
}


class StandaloneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = TemporaryDirectory(prefix="embeddedskills isolated ")
        cls.addClassCleanup(temporary.cleanup)
        cls.base = Path(temporary.name)
        cls.skills = {}
        for zip_path in build_archives(ROOT, cls.base / "archives", "standalone"):
            if zip_path.name == "embeddedskills-standalone.zip":
                continue
            name = zip_path.name.removeprefix("embeddedskills-").removesuffix("-standalone.zip")
            parent = cls.base / "install" / name
            with ZipFile(zip_path) as archive:
                archive.extractall(parent)
            cls.skills[name] = parent / name

    def setUp(self):
        temporary = TemporaryDirectory(prefix="workspace ", dir=self.base)
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)

    def cli(self, skill, script, *args):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([sys.executable, str(self.skills[skill] / "scripts" / script), *map(str, args)],
                              cwd=self.workspace, env=env, text=True, encoding="utf-8",
                              capture_output=True, timeout=25)

    def json_cli(self, skill, script, *args, success=True):
        call = self.cli(skill, script, *args)
        self.assertEqual(call.returncode == 0, success, (call.stdout, call.stderr))
        payload = json.loads(call.stdout)
        self.assertTrue({"status", "action", "summary", "details"} <= payload.keys(), payload)
        self.assertTrue(payload["summary"])
        self.assertEqual(payload["status"], "ok" if success else "error")
        if not success:
            self.assertTrue({"code", "message"} <= payload["error"].keys())
        return payload

    def terminal(self, *args, success=True, config=None):
        prefix = ["--workspace", self.workspace, "--json"]
        if config:
            prefix.extend(["--ssh-config", config])
        return self.json_cli("terminal", "terminal.py", *prefix, *args, success=success)

    def test_each_package_help_and_failure(self):
        self.assertEqual(set(self.skills), set(ENTRYPOINTS))
        for skill, (entry, args) in ENTRYPOINTS.items():
            scripts = self.skills[skill] / "scripts"
            parent_contents = {entry.name for entry in self.skills[skill].parent.iterdir()}
            self.assertEqual(parent_contents, {skill, "LICENSE"})
            for script in sorted(scripts.glob("*.py")):
                if "if __name__" not in script.read_text(encoding="utf-8"):
                    continue
                if script.name in {"terminal_sessions.py"}:
                    continue
                with self.subTest(skill=skill, entry=script.name):
                    response = self.cli(skill, script.name, "--help")
                    self.assertEqual(response.returncode, 0, response.stderr)
                    self.assertIn("usage:", response.stdout.lower())
                    self.json_cli(skill, script.name, "--unknown-skill-test-option", "--json", success=False)
            with self.subTest(skill=skill, entry=entry, failure=True):
                self.json_cli(skill, entry, *args, success=False)

    def test_local_commands_sessions_hex_timeout_and_log(self):
        result = self.terminal("exec", "local", "--", sys.executable, "-c", "print(42)")
        self.assertEqual(result["details"]["stdout"].strip(), "42")
        result = self.terminal("exec", "local", "--timeout", "0.1", "--", sys.executable,
                               "-c", "import time; time.sleep(20)", success=False)
        self.assertEqual(result["error"]["code"], "timeout")

        log = self.workspace / "capture.bin"
        self.terminal("start", "local", "--name", "echo", "--log", log, "--", sys.executable, "-u", "-c",
                      "import sys\nfor line in sys.stdin.buffer:\n sys.stdout.buffer.write(line); sys.stdout.buffer.flush()")
        try:
            self.terminal("start", "local", "--name", "echo", success=False)
            response = self.terminal("read", "echo", "--timeout", "0.05")
            self.assertTrue(response["details"]["timed_out"])
            self.terminal("send", "echo", "FF A0", "--hex", "--eol", "lf")
            response = self.terminal("read", "echo", "--timeout", "2", "--hex")
            self.assertEqual(response["details"]["hex"], "ff a0 0a")
            self.assertTrue(self.terminal("status", "echo")["details"]["alive"])
            self.assertEqual(log.read_bytes(), b"\xff\xa0\n")
        finally:
            self.terminal("stop", "echo")
        self.assertIn("session_not_found", self.terminal("status", "echo", success=False)["error"]["code"])

    def test_serial_scan_exclusive_open_hex_and_logging(self):
        test_source = r"""
import io, os, sys
from unittest.mock import MagicMock, patch
sys.path.insert(0, sys.argv[1])
import terminal_sessions as sessions
import terminal_transport as transport
import terminal
port = MagicMock(device="TEST", description="Simulated", hwid="TEST")
options = {"backend": "serial", "name": "board", "port": "TEST", "baudrate": 115200,
           "parity": "N", "bytesize": 8, "stopbits": 1}
serial, lister = MagicMock(), MagicMock()
lister.comports.return_value = [port]
with patch.object(transport, "serial_module", return_value=(serial, lister)):
    assert transport.scan_serial()[0]["port"] == "TEST"
    serial.Serial.side_effect = PermissionError("port in use")
    try:
        sessions.Session(options, None)
    except sessions.TerminalError as exc:
        assert exc.code == "serial_open_failed", exc
    else:
        raise AssertionError("port should be unavailable")
    serial.Serial.side_effect = None
    serial.Serial.return_value.read.return_value = b""
    with patch.object(transport.threading.Thread, "start"), patch.object(transport.threading.Thread, "join"):
        log = io.BytesIO()
        session = sessions.Session(options, log)
        if os.name != "nt":
            assert serial.Serial.call_args.kwargs["exclusive"] is True
        with patch.object(sessions, "MAX_BUFFER", 2):
            session.receive(bytes.fromhex("AA FF 01"))
        response = session.handle({"action": "read", "timeout": 0, "hex": True})
        assert response["details"]["hex"] == "ff 01"
        assert response["details"]["dropped_bytes"] == 1
        assert log.getvalue() == bytes.fromhex("AA FF 01")
        with patch.object(sessions, "call", side_effect=lambda _, action, **data: session.handle({"action": action, **data})):
            terminal.transmit({}, "FE", "utf-8", "crlf", hex_mode=True)
        serial.Serial.return_value.write.assert_called_once_with(b"\xfe\r\n")
        session.close()
"""
        args = [sys.executable, "-I", "-B", "-c", test_source, str(self.skills["terminal"] / "scripts")]
        response = subprocess.run(args, cwd=self.workspace, capture_output=True, text=True, timeout=15)
        self.assertEqual(response.returncode, 0, response.stderr)

    def test_workflow_discovers_and_plans_three_build_backends_read_only(self):
        projects = self.workspace
        (projects / "gcc").mkdir()
        (projects / "gcc" / "CMakeLists.txt").touch()
        (projects / "gcc" / "CMakePresets.json").write_text(json.dumps({
            "version": 3,
            "configurePresets": [{"name": "configure", "generator": "Ninja", "binaryDir": "build"}],
            "buildPresets": [{"name": "debug", "configurePreset": "configure"}],
        }), encoding="utf-8")
        (projects / "board.uvprojx").write_text("<Project/>", encoding="utf-8")
        (projects / "eide" / ".eide").mkdir(parents=True)
        (projects / "eide" / ".eide" / "eide.yml").touch()
        params = projects / "eide" / "build" / "debug" / "builder.params"
        params.parent.mkdir(parents=True)
        params.write_text("{}", encoding="utf-8")
        config = self.skills["workflow"] / "config.json"
        config.write_text(json.dumps({"tools": {name: sys.executable for name in (
            "cmake", "uv4", "unify_builder", "openocd", "probe_rs", "gdb")}}), encoding="utf-8")
        self.addCleanup(config.unlink)
        snapshot = sorted((file.relative_to(projects), file.read_bytes()) for file in projects.rglob("*") if file.is_file())

        def call(action, *args):
            return self.json_cli("workflow", "workflow.py", action, "--json", *args)["details"]

        ambiguous = call("plan")["steps"][0]
        self.assertEqual(set(ambiguous["candidates"]), {"gcc", "keil", "eide"})
        self.assertEqual(ambiguous["status"], "blocked")
        for backend, expected in (("gcc", [["--preset", "configure"], ["--build", "--preset", "debug"]]),
                                  ("keil", [["-b", str(projects / "board.uvprojx"), "-j0", "-t", "debug"]]),
                                  ("eide", [["build", "--params-file", str(params)]])):
            step = call("plan", "--build-backend", backend, "--build-profile", "debug")["steps"][0]
            self.assertEqual(step["status"], "ready", step)
            self.assertEqual([command["argv"][1:] for command in step["commands"]], expected)
        self.assertTrue(call("diagnose")["issues"])
        self.assertEqual(snapshot, sorted((file.relative_to(projects), file.read_bytes())
                                          for file in projects.rglob("*") if file.is_file()))
        self.assertFalse((projects / ".embeddedskills").exists())

    def test_eide_project_config_precedes_machine_defaults_and_preserves_error_exit(self):
        path = self.workspace / ".embeddedskills" / "config.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"eide": {"project": "selected", "config": "Selected"}}), encoding="utf-8")
        source = r"""
import sys
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import eide_build

def mock_builder(**args):
    assert args["project"] == str(Path.cwd() / "selected"), args
    assert args["config"] == "Selected", args
    return {"status": "error", "error": {"code": "simulated_build_failure", "message": "mock failure"}}

sys.argv = ["eide_build.py", "build", "--json"]
with patch.object(eide_build, "load_local_config", return_value={
    "builder_exe": sys.executable, "default_project": "wrong", "default_config": "Wrong"
}), patch.object(eide_build, "run_builder", side_effect=mock_builder):
    eide_build.main()
"""
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", source, str(self.skills["eide"] / "scripts")],
                              cwd=self.workspace, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "simulated_build_failure")

    def test_skill_states_are_independent(self):
        for name in ENTRYPOINTS.keys() - {"terminal", "workflow"}:
            with self.subTest(skill=name):
                prefix = name.replace("-", "_")
                scripts = str(self.skills[name] / "scripts")
                source = (
                    "import sys, pathlib, importlib; sys.path.insert(0, sys.argv[1]); "
                    "module = importlib.import_module(sys.argv[2]); "
                    "module.save_workspace_state({'last_build': {'provider': sys.argv[3]}}, sys.argv[4]); "
                    "assert module.load_workspace_state(sys.argv[4])['last_build']['provider'] == sys.argv[3]"
                )
                proc = subprocess.run([sys.executable, "-I", "-B", "-c", source, scripts,
                                       f"{prefix}_runtime", name, str(self.workspace)],
                                      cwd=self.workspace, capture_output=True, timeout=15)
                self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ENTRYPOINTS.keys() - {"terminal", "workflow"}:
            path = self.workspace / ".embeddedskills" / "state" / f"{name}.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["last_build"]["provider"], name)

    def test_skill_project_config_updates_from_separate_processes(self):
        source = (
            "import sys, importlib; sys.path.insert(0, sys.argv[1]); "
            "module = importlib.import_module(sys.argv[2]); "
            "[module.save_project_config(sys.argv[3], {'setting': i}) for i in range(20)]"
        )
        processes = [subprocess.Popen([sys.executable, "-I", "-B", "-c", source,
                                       str(self.skills[name] / "scripts"), f"{name}_runtime", str(self.workspace)],
                                      cwd=self.workspace, stderr=subprocess.PIPE) for name in ("can", "serial")]
        for process in processes:
            _, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
        path = self.workspace / ".embeddedskills" / "config.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         {"can": {"setting": 19}, "serial": {"setting": 19}})

    @unittest.skipUnless(importlib.util.find_spec("asyncssh") and shutil.which("ssh") and shutil.which("scp"),
                         "需要 AsyncSSH 和 OpenSSH 客户端")
    def test_loopback_ssh_commands_session_transfer_and_tunnel(self):
        import asyncssh

        async def exercise():
            identity = asyncssh.generate_private_key("ssh-ed25519")
            host = asyncssh.generate_private_key("ssh-ed25519")
            identity_path = self.workspace / "identity"
            identity.write_private_key(identity_path)
            if os.name == "nt":
                token = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"])
                sid = re.search(rb"S-1-[0-9-]+", token)[0].decode("ascii")
                subprocess.run(["icacls", str(identity_path), "/inheritance:r", "/grant:r", f"*{sid}:(R,W)"],
                               check=True, capture_output=True)
            else:
                identity_path.chmod(0o600)
            known = self.workspace / "known_hosts"
            config = self.workspace / "ssh_config"
            sftp_root = self.workspace / "remote"
            sftp_root.mkdir()

            class Server(asyncssh.SSHServer):
                def public_key_auth_supported(self):
                    return True

                def validate_public_key(self, username, key):
                    return username == "tester" and key == identity.convert_to_public()

                def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
                    return dest_host == "127.0.0.1" and dest_port == echo_port

            class SftpServer(asyncssh.SFTPServer):
                def exit(self):
                    self.channel.exit(0)

            async def process_handler(process):
                if process.command:
                    process.stdout.write("os=TestOS\n" if "value os" in process.command else "remote command ran\n")
                    process.exit(0)
                    return
                process.stdout.write("ready\n")
                async for line in process.stdin:
                    process.stdout.write("echo " + line.strip() + "\n")
                process.exit(0)

            async def echo(reader, writer):
                try:
                    if data := await reader.read(4096):
                        writer.write(data)
                        await writer.drain()
                finally:
                    writer.close()

            echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
            echo_port = echo_server.sockets[0].getsockname()[1]
            ssh_server = await asyncssh.listen(
                "127.0.0.1", 0, server_factory=Server, server_host_keys=[host],
                process_factory=process_handler,
                sftp_factory=lambda channel: SftpServer(channel, chroot=os.fsencode(sftp_root)),
            )
            ssh_port = ssh_server.get_port()
            known.write_bytes(f"[127.0.0.1]:{ssh_port} ".encode() + host.export_public_key())
            config.write_text(f'Host test\n    HostName 127.0.0.1\n    User tester\n    Port {ssh_port}\n'
                              f'    IdentityFile "{identity_path.as_posix()}"\n'
                              f'    UserKnownHostsFile "{known.as_posix()}"\n', encoding="utf-8")

            async def term(*args, success=True):
                return await asyncio.to_thread(self.terminal, *args, success=success, config=config)

            try:
                resolved = await term("ssh", "hosts", "resolve", "test")
                self.assertEqual(resolved["details"]["host"]["alias"], "test")
                response = await term("exec", "ssh", "test", "sample")
                self.assertIn("remote command ran", response["details"]["stdout"])
                self.assertEqual((await term("ssh", "probe", "test"))["details"]["environment"]["os"], "TestOS")
                await term("start", "ssh", "test", "--name", "failed", "--known-hosts-file", self.workspace / "unknown",
                           success=False)
                self.assertEqual((await term("status", "failed", success=False))["error"]["code"], "session_not_found")
                await term("start", "ssh", "test", "--name", "ssh-test")
                try:
                    await term("send", "ssh-test", "hello", "--eol", "lf")
                    output = ""
                    for _ in range(4):
                        output += (await term("read", "ssh-test", "--timeout", "1"))["details"]["text"]
                        if "echo hello" in output:
                            break
                    self.assertIn("echo hello", output)
                finally:
                    await term("stop", "ssh-test")

                uploaded = self.workspace / "upload local.bin"
                uploaded.write_bytes(b"\x00ssh\xff")
                await term("ssh", "upload", "test", uploaded, "/remote copy.bin")
                downloaded = self.workspace / "download local.bin"
                await term("ssh", "download", "test", downloaded, "/remote copy.bin")
                self.assertEqual(downloaded.read_bytes(), uploaded.read_bytes())

                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    local_port = probe.getsockname()[1]
                await term("ssh", "tunnel", "start", "test", "--name", "forward",
                           "--local-port", local_port, "--remote-port", echo_port)
                try:
                    self.assertTrue((await term("ssh", "tunnel", "status", "--name", "forward"))["details"]["alive"])
                    reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
                    writer.write(b"forward")
                    await writer.drain()
                    self.assertEqual(await asyncio.wait_for(reader.readexactly(7), 5), b"forward")
                    writer.close()
                    await writer.wait_closed()
                finally:
                    await term("ssh", "tunnel", "stop", "--name", "forward")
            finally:
                ssh_server.close()
                await ssh_server.wait_closed()
                echo_server.close()
                await echo_server.wait_closed()

        asyncio.run(exercise())
