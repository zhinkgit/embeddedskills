"""workflow check 阶段单测：不依赖 codecheck 实际执行。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import workflow_run  # noqa: E402


class CheckFailOnTests(unittest.TestCase):
    def test_cli_wins(self):
        config = {"check_fail_on": "warning"}
        self.assertEqual(workflow_run.select_check_fail_on(config, "error"), "error")

    def test_workflow_config_is_used(self):
        self.assertEqual(workflow_run.select_check_fail_on({"check_fail_on": "none"}, None), "none")

    def test_invalid_config_value_is_ignored(self):
        self.assertIsNone(workflow_run.select_check_fail_on({"check_fail_on": "blocker"}, None))
        self.assertIsNone(workflow_run.select_check_fail_on({}, None))


class CheckScriptTests(unittest.TestCase):
    def test_script_path_points_to_codecheck_skill(self):
        path = workflow_run.check_script_path()
        self.assertEqual(path.parts[-3:], ("codecheck", "scripts", "codecheck.py"))

    def test_missing_skill_returns_actionable_error(self):
        with mock.patch.object(workflow_run, "check_script_path", return_value=Path("missing-codecheck.py")):
            result = workflow_run.run_check(Path(tempfile.mkdtemp()), {}, None)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "check_not_available")

    def test_fail_on_is_forwarded_and_result_is_returned(self):
        workspace = Path(tempfile.mkdtemp())
        script = workspace / "codecheck.py"
        script.write_text("# fake\n", encoding="utf-8")
        captured = {}

        def fake_run_json(cmd, workdir):
            captured["cmd"] = cmd
            return {"status": "ok", "action": "check", "summary": "规范检查通过"}

        with mock.patch.object(workflow_run, "check_script_path", return_value=script), mock.patch.object(
            workflow_run, "run_json", side_effect=fake_run_json
        ):
            result = workflow_run.run_check(workspace, {"workflow": {"check_fail_on": "error"}}, None)

        self.assertEqual(result["status"], "ok")
        self.assertIn("--fail-on", captured["cmd"])
        self.assertEqual(captured["cmd"][captured["cmd"].index("--fail-on") + 1], "error")

    def test_unexpected_result_is_mapped_to_error(self):
        workspace = Path(tempfile.mkdtemp())
        script = workspace / "codecheck.py"
        script.write_text("# fake\n", encoding="utf-8")
        with mock.patch.object(workflow_run, "check_script_path", return_value=script), mock.patch.object(
            workflow_run, "run_json", return_value={"status": "error", "action": "subprocess", "error": {"code": "invalid_json", "message": "bad"}}
        ):
            result = workflow_run.run_check(workspace, {}, None)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "invalid_check_result")


if __name__ == "__main__":
    unittest.main(verbosity=2)