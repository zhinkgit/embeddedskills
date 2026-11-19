from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "workflow_under_test", ROOT / "workflow" / "scripts" / "workflow.py"
)
workflow = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(workflow)


class WorkflowVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.plan_dir = self.workspace / ".embeddedskills" / "verify"
        self.plan_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path: Path, value: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def run_workflow(self, *args: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = workflow.main([*args, "--workspace", str(self.workspace)])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def verify_step(self, payload: dict) -> dict:
        return next(step for step in payload["details"]["steps"] if step["stage"] == "verify")

    def test_plan_blocks_verify_without_a_plan(self):
        step = self.verify_step(self.run_workflow("plan"))
        self.assertEqual(step["status"], "blocked")
        self.assertTrue(any("verify 计划" in item for item in step["requires"]))

    def test_plan_emits_read_only_verify_command(self):
        plan = self.plan_dir / "plan.json"
        self.write_json(plan, {"unit": {"command": ["python", "-c", "print(1)"]}})
        step = self.verify_step(self.run_workflow("plan"))
        self.assertEqual(step["status"], "ready")
        self.assertEqual(step["action"], "run")
        command = step["commands"][0]["argv"]
        self.assertIn(str(plan), command)
        self.assertIn("verify.py", command[1])
        self.assertEqual(command[2], "run")

    def test_cli_action_overrides_config(self):
        self.write_json(self.plan_dir / "plan.json", {"unit": {"command": ["python"]}})
        self.write_json(self.workspace / ".embeddedskills" / "config.json",
                        {"verify": {"action": "unit"}})
        step = self.verify_step(self.run_workflow("plan", "--verify-action", "smoke"))
        self.assertEqual(step["action"], "smoke")
        self.assertEqual(step["commands"][0]["argv"][2], "smoke")

    def test_config_action_and_relative_plan_are_resolved(self):
        self.write_json(self.plan_dir / "plan.json", {"unit": {"command": ["python"]}})
        self.write_json(self.workspace / ".embeddedskills" / "config.json",
                        {"verify": {"action": "unit",
                                    "plan": ".embeddedskills/verify/plan.json"}})
        step = self.verify_step(self.run_workflow("plan"))
        self.assertEqual(step["action"], "unit")
        self.assertEqual(step["plan"], str(self.plan_dir / "plan.json"))

    def test_invalid_configured_action_blocks_verify(self):
        self.write_json(self.plan_dir / "plan.json", {"unit": {"command": ["python"]}})
        self.write_json(self.workspace / ".embeddedskills" / "config.json",
                        {"verify": {"action": "stress"}})
        step = self.verify_step(self.run_workflow("plan"))
        self.assertEqual(step["status"], "blocked")
        self.assertTrue(any("verify.action" in item for item in step["requires"]))

    def test_missing_verify_script_blocks_verify(self):
        self.write_json(self.plan_dir / "plan.json", {"unit": {"command": ["python"]}})
        missing = self.workspace / "missing-verify.py"
        with mock.patch.object(workflow, "verify_script", return_value=missing):
            step = self.verify_step(self.run_workflow("plan"))
        self.assertEqual(step["status"], "blocked")
        self.assertTrue(any("verify 脚本" in item for item in step["requires"]))

    def test_diagnose_includes_verify_issue(self):
        payload = self.run_workflow("diagnose")
        issues = {issue["stage"] for issue in payload["details"]["issues"]}
        self.assertIn("verify", issues)


if __name__ == "__main__":
    unittest.main()