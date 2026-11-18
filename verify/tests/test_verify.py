"""verify skill 的单元测试：不依赖真实硬件。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify  # noqa: E402
from verify_plan import (  # noqa: E402
    PlanError,
    load_plan,
    resolve_serial_settings,
    resolve_unit_settings,
    validate_plan,
)
from verify_runners import (  # noqa: E402
    RunnerError,
    build_reset_command,
    detect_unit_command,
    execute_smoke,
    parse_junit_xml,
    run_unit,
    scan_workspace,
)
from verify_runtime import (  # noqa: E402
    ConfigError,
    load_json_file,
    report_dir,
    resolve_plan_path,
    save_json_file,
    write_report,
)

PASS_JUNIT = (
    '<?xml version="1.0"?>'
    '<testsuite tests="1" failures="0" errors="0" skipped="0">'
    '<testcase name="t1" classname="Suite" time="0.02"/>'
    "</testsuite>"
)
FAIL_JUNIT = (
    '<?xml version="1.0"?>'
    '<testsuite tests="1" failures="1" errors="0" skipped="0">'
    '<testcase name="t1" classname="Suite" time="0.02">'
    '<failure message="expected 1">trace</failure>'
    "</testcase></testsuite>"
)

PASS_SCRIPT = (
    "import sys\n"
    "from pathlib import Path\n"
    "Path(sys.argv[1]).write_text(" + repr(PASS_JUNIT) + ", encoding='utf-8')\n"
)
FAIL_SCRIPT = (
    "import sys\n"
    "from pathlib import Path\n"
    "Path(sys.argv[1]).write_text(" + repr(FAIL_JUNIT) + ", encoding='utf-8')\n"
    "sys.exit(1)\n"
)


def write_config(workspace: Path, data: dict) -> None:
    directory = workspace / ".embeddedskills"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(json.dumps(data), encoding="utf-8")


def write_plan(workspace: Path, plan: dict) -> Path:
    path = workspace / ".embeddedskills" / "verify" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def unit_plan(script: str) -> dict:
    return {
        "name": "unit",
        "unit": {
            "command": [sys.executable, "-c", script, "{junit}"],
            "timeout_sec": 30,
        },
    }


def smoke_plan(cases: list[dict], *, fail_fast: bool = False) -> dict:
    return {
        "name": "smoke",
        "smoke": {
            "serial": {"port": "COMX", "baudrate": 115200},
            "cases": cases,
            "fail_fast": fail_fast,
        },
    }


class FakeTransport:
    def __init__(self, config: dict):
        self.config = config
        self.written: list[bytes] = []
        self.incoming = "BOOT READY\nOK\n"
        self.closed = False
        self.opened = False
        self.flushed = 0

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def write(self, payload: bytes) -> None:
        self.written.append(payload)
        self.incoming += "OK\n"

    def flush_input(self) -> None:
        self.flushed += 1
        self.incoming = ""

    def read_until(self, pattern, timeout_sec: float, encoding: str):
        match = pattern.search(self.incoming)
        if match:
            return True, self.incoming, match.group(0)
        return False, self.incoming, ""


class RuntimeTests(unittest.TestCase):
    def test_load_json_file_missing_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_json_file(Path(directory) / "missing.json"), {})

    def test_load_json_file_required_missing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError):
                load_json_file(Path(directory) / "missing.json", required=True)

    def test_load_json_file_corrupt_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_json_file(path)

    def test_load_json_file_non_object_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "list.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_json_file(path)

    def test_save_json_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            save_json_file(path, {"ok": True, "text": "中文"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True, "text": "中文"})

    def test_resolve_plan_path_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_config(root, {"verify": {"plan": "custom/plan.json"}})
            candidate = root / ".embeddedskills" / "verify" / "plan.json"
            self.assertEqual(resolve_plan_path(str(root)), root / "custom" / "plan.json")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("{}", encoding="utf-8")
            explicit = root / "explicit.json"
            self.assertEqual(resolve_plan_path(str(root), str(explicit)), explicit)
            write_config(root, {})
            self.assertEqual(resolve_plan_path(str(root)), candidate)

    def test_write_report_writes_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_report(str(root), {"ok": True}, prefix="unit")
            self.assertTrue(Path(paths["report"]).is_file())
            latest = Path(paths["latest_report"])
            self.assertEqual(latest, report_dir(str(root)) / "latest.json")
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"ok": True})


class PlanTests(unittest.TestCase):
    def test_valid_unit_and_smoke_plan(self) -> None:
        plan = {
            "name": "p",
            "unit": {"command": ["ctest"], "timeout_sec": 10},
            "smoke": {
                "serial": {"port": "COM1"},
                "reset": {"backend": "none"},
                "cases": [{"name": "boot", "steps": [{"serial_expect": {"contains": "OK"}}]}],
            },
        }
        validate_plan(plan)

    def test_plan_requires_unit_or_smoke(self) -> None:
        with self.assertRaises(PlanError):
            validate_plan({"name": "empty"})

    def test_plan_rejects_unknown_top_field(self) -> None:
        with self.assertRaises(PlanError):
            validate_plan({"unit": {"command": ["x"]}, "extra": 1})

    def test_plan_rejects_invalid_regex(self) -> None:
        plan = smoke_plan([{"name": "c", "steps": [{"serial_expect": {"regex": "["}}]}])
        with self.assertRaises(PlanError):
            validate_plan(plan)

    def test_plan_rejects_empty_reset_is_allowed(self) -> None:
        plan = smoke_plan([{"name": "c", "steps": [{"reset": {}}]}])
        validate_plan(plan)

    def test_plan_accepts_empty_unit_for_autodetect(self) -> None:
        validate_plan({"unit": {}})

    def test_plan_rejects_null_reset(self) -> None:
        plan = smoke_plan([{"name": "c", "steps": [{"reset": {}}]}])
        plan["smoke"]["reset"] = None
        with self.assertRaises(PlanError):
            validate_plan(plan)

    def test_resolve_unit_settings_priority_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_config(root, {"verify": {"junit": "out/config.xml"}})
            plan = {"unit": {"command": ["run", "{workspace}", "{junit}"], "junit": "out/plan.xml"}}
            settings = resolve_unit_settings(plan, str(root))
            self.assertTrue(settings["junit"].endswith("out\\plan.xml") or settings["junit"].endswith("out/plan.xml"))
            self.assertEqual(settings["command"][1], str(root.resolve()))
            self.assertTrue(settings["command"][2].endswith("plan.xml"))
            self.assertTrue(resolve_unit_settings(plan, str(root), cli_junit="out/cli.xml")["junit"].endswith("cli.xml"))

    def test_resolve_serial_settings_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_config(
                root,
                {
                    "serial": {"port": "COM1", "baudrate": 9600},
                    "verify": {"serial": {"port": "COM2"}},
                },
            )
            plan = {"smoke": {"serial": {"port": "COM3"}, "cases": [{"name": "c", "steps": [{"sleep": {"ms": 1}}]}]}}
            settings, sources = resolve_serial_settings(plan, str(root))
            self.assertEqual(settings["port"], "COM3")
            self.assertEqual(settings["baudrate"], 9600)
            self.assertEqual(sources["port"], "plan")
            cli_settings, _ = resolve_serial_settings(plan, str(root), cli_port="COM4")
            self.assertEqual(cli_settings["port"], "COM4")
            no_plan_port = {"smoke": {"cases": [{"name": "c", "steps": [{"sleep": {"ms": 1}}]}]}}
            config_settings, _ = resolve_serial_settings(no_plan_port, str(root))
            self.assertEqual(config_settings["port"], "COM2")

    def test_load_plan_reads_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_plan(root, unit_plan(PASS_SCRIPT))
            loaded = load_plan(path)
            self.assertEqual(loaded["name"], "unit")


class RunnerTests(unittest.TestCase):
    def test_parse_junit_namespace_and_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "junit.xml"
            path.write_text(
                '<?xml version="1.0"?>'
                '<testsuites xmlns="http://example.com/junit">'
                '<testsuite tests="4" failures="1" errors="1" skipped="1">'
                '<testcase name="ok" classname="C" time="0.02"/>'
                '<testcase name="fail" classname="C"><failure message="bad">trace</failure></testcase>'
                '<testcase name="err" classname="C"><error message="boom"/></testcase>'
                '<testcase name="skip" classname="C"><skipped message="later"/></testcase>'
                "</testsuite></testsuites>",
                encoding="utf-8",
            )
            info = parse_junit_xml(path)
            self.assertEqual(info["totals"], {"tests": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1})
            statuses = [case["status"] for case in info["cases"]]
            self.assertEqual(statuses, ["passed", "failed", "error", "skipped"])

    def test_detect_unit_command_ctest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            (root / "build" / "CTestTestfile.cmake").write_text("", encoding="utf-8")
            command, framework = detect_unit_command(root, root / "junit.xml")
            self.assertEqual(framework, "ctest")
            self.assertEqual(command[0], "ctest")
            self.assertIn("--output-junit", command)

    def test_detect_unit_command_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            (root / "tests").mkdir()
            command, framework = detect_unit_command(root, root / "junit.xml")
            self.assertEqual(framework, "pytest")
            self.assertEqual(command[1:3], ["-m", "pytest"])

    def test_detect_unit_command_none_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RunnerError):
                detect_unit_command(Path(directory), Path(directory) / "junit.xml")

    def test_run_unit_passing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_unit(unit_plan(PASS_SCRIPT), root)
            self.assertTrue(result["passed"])
            self.assertEqual(result["totals"]["tests"], 1)
            self.assertTrue(result["junit"]["available"])

    def test_run_unit_failing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_unit(unit_plan(FAIL_SCRIPT), root)
            self.assertFalse(result["passed"])
            self.assertEqual(result["totals"]["failed"], 1)
            self.assertEqual(result["error"]["code"], "unit_failed")

    def test_run_unit_dry_run_skips_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_unit(unit_plan(PASS_SCRIPT), root, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertFalse(Path(root / ".embeddedskills" / "reports" / "verify" / "unit.xml").exists())

    def test_build_reset_command_custom_and_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = build_reset_command(
                {"command": [sys.executable, "-c", "print(1)", "{workspace}"]},
                {},
                root,
                {},
            )
            self.assertEqual(command[-1], str(root))
            self.assertIsNone(build_reset_command({"backend": "none"}, {}, root, {}))

    def test_reset_step_overrides_smoke_step_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smoke_reset = {"command": [sys.executable, "-c", "print('smoke')"]}
            self.assertIsNone(build_reset_command({"backend": "none"}, smoke_reset, root, {}))
            jlink_command = build_reset_command(
                {"backend": "jlink"},
                smoke_reset,
                root,
                {"jlink": {"device": "STM32F407VG"}},
            )
            self.assertIn("jlink_exec.py", " ".join(jlink_command))

    def test_build_reset_command_infer_jlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = build_reset_command({}, {}, root, {"jlink": {"device": "STM32F407VG"}})
            self.assertIn("jlink_exec.py", " ".join(command))
            self.assertIn("STM32F407VG", command)

    def test_build_reset_command_without_config_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RunnerError):
                build_reset_command({}, {}, Path(directory), {})


class SmokeTests(unittest.TestCase):
    def _run(self, plan: dict, **kwargs):
        holder: dict = {}

        def factory(settings):
            transport = FakeTransport(settings)
            holder["transport"] = transport
            return transport

        result = execute_smoke(plan, kwargs.pop("workspace"), transport_factory=factory, **kwargs)
        return result, holder.get("transport")

    def test_smoke_passes_and_sends_line_ending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan(
                [
                    {
                        "name": "boot",
                        "steps": [
                            {"serial_send": {"data": "AT", "line_ending": "crlf"}},
                            {"serial_expect": {"contains": "OK", "timeout_sec": 1}},
                        ],
                    }
                ]
            )
            result, transport = self._run(plan, workspace=directory)
            self.assertTrue(result["passed"])
            self.assertEqual(transport.written, [b"AT\r\n"])
            self.assertTrue(transport.closed)

    def test_smoke_failure_reports_received_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan(
                [{"name": "boot", "steps": [{"serial_expect": {"contains": "NOPE", "timeout_sec": 0.1}}]}]
            )
            result, transport = self._run(plan, workspace=directory)
            self.assertFalse(result["passed"])
            self.assertEqual(result["failed_cases"], 1)
            self.assertIn("NOPE", result["cases"][0]["error"])
            self.assertTrue(transport.closed)

    def test_smoke_fail_fast_stops_after_first_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan(
                [
                    {"name": "first", "steps": [{"serial_expect": {"contains": "NOPE", "timeout_sec": 0.1}}]},
                    {"name": "second", "steps": [{"serial_expect": {"contains": "OK", "timeout_sec": 1}}]},
                ],
                fail_fast=True,
            )
            result, _ = self._run(plan, workspace=directory)
            self.assertFalse(result["passed"])
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["cases"][0]["name"], "first")

    def test_smoke_only_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan(
                [
                    {"name": "first", "steps": [{"serial_expect": {"contains": "OK", "timeout_sec": 1}}]},
                    {"name": "second", "steps": [{"serial_expect": {"contains": "NOPE", "timeout_sec": 0.1}}]},
                ]
            )
            result, _ = self._run(plan, workspace=directory, only_case="second")
            self.assertFalse(result["passed"])
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["cases"][0]["name"], "second")

    def test_smoke_dry_run_does_not_open_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan([{"name": "boot", "steps": [{"sleep": {"ms": 1}}]}])
            result = execute_smoke(plan, directory, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertEqual(len(result["cases"]), 1)

    def test_smoke_reset_none_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan([{"name": "boot", "steps": [{"reset": {"backend": "none"}}, {"sleep": {"ms": 1}}]}])
            result, _ = self._run(plan, workspace=directory)
            self.assertTrue(result["passed"])

    def test_smoke_run_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = smoke_plan(
                [{"name": "run", "steps": [{"run": {"command": [sys.executable, "-c", "print('x')"]}}]}]
            )
            result, _ = self._run(plan, workspace=directory)
            self.assertTrue(result["passed"])


class DiscoveryTests(unittest.TestCase):
    def test_scan_workspace_finds_plan_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, unit_plan(PASS_SCRIPT))
            (root / ".embeddedskills").mkdir(exist_ok=True)
            (root / ".embeddedskills" / "state").mkdir(exist_ok=True)
            (root / ".embeddedskills" / "state" / "verify.json").write_text(
                json.dumps({"last_build": {"status": "ok"}}), encoding="utf-8"
            )
            scan = scan_workspace(root)
            self.assertTrue(scan["plan"].endswith("plan.json"))
            self.assertEqual(scan["state"]["last_build"]["status"], "ok")
            self.assertIn("ctest", scan["tools"])


class CliTests(unittest.TestCase):
    def _call(self, argv: list[str]) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = verify.main(argv)
        return code, output.getvalue()

    def test_scan_without_plan_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = self._call(["scan", "--workspace", directory, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["action"], "scan")

    def test_unit_writes_state_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, unit_plan(PASS_SCRIPT))
            code, output = self._call(["unit", "--workspace", directory, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(Path(payload["artifacts"]["report"]).is_file())
            state = json.loads((root / ".embeddedskills" / "state" / "verify.json").read_text(encoding="utf-8"))
            self.assertTrue(state["last_verify"]["passed"])

    def test_unit_failure_returns_one_and_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, unit_plan(FAIL_SCRIPT))
            code, output = self._call(["unit", "--workspace", directory, "--json"])
            self.assertEqual(code, 1)
            payload = json.loads(output)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["code"], "unit_failed")
            state = json.loads((root / ".embeddedskills" / "state" / "verify.json").read_text(encoding="utf-8"))
            self.assertFalse(state["last_verify"]["passed"])

    def test_missing_plan_returns_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = self._call(["run", "--workspace", directory, "--json"])
            self.assertEqual(code, 1)
            payload = json.loads(output)
            self.assertEqual(payload["error"]["code"], "config_invalid")

    def test_smoke_dry_run_does_not_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, smoke_plan([{"name": "boot", "steps": [{"sleep": {"ms": 1}}]}]))
            code, output = self._call(["smoke", "--workspace", directory, "--json", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertFalse((root / ".embeddedskills" / "state" / "verify.json").exists())

    def test_unit_without_junit_reports_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, {"name": "unit", "unit": {"command": [sys.executable, "-c", "print('ok')"]}})
            code, output = self._call(["unit", "--workspace", directory, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertIn("未生成 JUnit", payload["summary"])

    def test_empty_unit_section_runs_autodetect_instead_of_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, {"name": "unit", "unit": {}})
            fake_unit = {
                "passed": True,
                "framework": "custom",
                "command": ["fake"],
                "working_dir": directory,
                "junit": {"available": False, "path": "", "totals": {}},
                "totals": {"tests": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0},
                "cases": [],
                "duration_ms": 1,
            }
            with mock.patch.object(verify, "run_unit", return_value=fake_unit):
                code, output = self._call(["unit", "--workspace", directory, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["action"], "unit")
            self.assertIn("unit", payload["details"])

    def test_report_action_reads_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_plan(root, unit_plan(PASS_SCRIPT))
            self._call(["unit", "--workspace", directory, "--json"])
            code, output = self._call(["report", "--workspace", directory, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["action"], "report")
            self.assertEqual(payload["details"]["action"], "unit")


if __name__ == "__main__":
    unittest.main()