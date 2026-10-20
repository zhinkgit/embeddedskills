from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import subprocess


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from probe_rs_exec import parse_output, run_command


class ProbeListTests(unittest.TestCase):
    def test_no_probe_and_warnings_are_not_devices(self):
        for output in ("", "No debug probes were found.", "No probes were found.", "WARN USB unavailable\nNo debug probes were found.\nWARN driver unavailable"):
            with self.subTest(output=output):
                self.assertEqual(parse_output(output, "list")["probes"], [])

    def test_only_numbered_entries_are_counted_including_color_output(self):
        output = "The following debug probes were found:\n[0]: CMSIS-DAP -- 1234:5678\nWARN USB warning\n\x1b[32m[1]: J-Link -- 1366:0105\x1b[0m\n"
        self.assertEqual(parse_output(output, "list")["probes"], ["[0]: CMSIS-DAP -- 1234:5678", "[1]: J-Link -- 1366:0105"])

    def test_empty_list_succeeds_and_preserves_raw_warnings(self):
        with patch("probe_rs_exec.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "No debug probes were found.", "WARN USB warning")):
            result = run_command("list", ["probe-rs", "list"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["details"]["probes"], [])
        self.assertIn("未检测到", result["summary"])
        self.assertIn("WARN USB warning", result["details"]["output"])

    def test_real_errors_and_non_list_no_probe_failures_are_preserved(self):
        self.assertEqual(parse_output("No probes were found.", "flash")["error_code"], "no_probe_found")
        with patch("probe_rs_exec.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "fatal failure")):
            self.assertEqual(run_command("list", ["probe-rs", "list"])["status"], "error")


if __name__ == "__main__":
    unittest.main()
