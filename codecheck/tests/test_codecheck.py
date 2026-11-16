"""codecheck 单测：不依赖本机 clang 工具链，工具调用通过 mock 注入。"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import codecheck  # noqa: E402
import codecheck_runtime  # noqa: E402
import codecheck_tools  # noqa: E402


class ParseTidyTests(unittest.TestCase):
    def test_parses_gcc_style_diagnostics(self):
        workspace = Path(tempfile.mkdtemp())
        text = "\n".join(
            [
                r"C:\ws\APP\app.c:42:9: error: use of undeclared identifier 'x' [clang-diagnostic-error]",
                "/ws/APP/app.c:7:5: warning: narrowing conversion [bugprone-narrowing-conversions]",
                "5 warnings generated.",
                "note: this is a note",
            ]
        )
        findings = codecheck_tools.parse_tidy_output(text, workspace)
        self.assertEqual(len(findings), 2)
        first = findings[0]
        self.assertEqual(first["severity"], "error")
        self.assertEqual(first["line"], 42)
        self.assertEqual(first["rule"], "clang-diagnostic-error")
        self.assertTrue(first["file"].endswith("app.c"))

    def test_ignores_noise_lines(self):
        workspace = Path(tempfile.mkdtemp())
        self.assertEqual(
            codecheck_tools.parse_tidy_output("Found compiler error(s).\nError while processing file\n", workspace),
            [],
        )


class GateTests(unittest.TestCase):
    def test_warning_fails_gate_at_warning_level(self):
        gate = codecheck_tools.evaluate_gate([{"severity": "warning"}, {"severity": "error"}], "warning")
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["counts"], {"error": 1, "warning": 1, "info": 0})

    def test_error_gate_passes_with_only_warnings(self):
        self.assertTrue(codecheck_tools.evaluate_gate([{"severity": "warning"}], "error")["passed"])

    def test_none_gate_always_passes(self):
        self.assertTrue(codecheck_tools.evaluate_gate([{"severity": "error"}], "none")["passed"])


class FormatTests(unittest.TestCase):
    def test_nonconforming_file_becomes_finding(self):
        workspace = Path(tempfile.mkdtemp())
        good = workspace / "good.c"
        bad = workspace / "bad.c"
        good.write_text("int a;\n", encoding="utf-8")
        bad.write_text("int  b ;\n", encoding="utf-8")

        def fake_run(command, timeout):
            target = Path(command[-1])
            return codecheck_tools.subprocess.CompletedProcess(command, 1 if target == bad else 0, "", "")

        with mock.patch.object(codecheck_tools, "run_process", side_effect=fake_run):
            findings, notes = codecheck_tools.format_findings([good, bad], "clang-format", "", workspace, 10)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "clang-format")
        self.assertEqual(findings[0]["file"], "bad.c")
        self.assertEqual(notes, [])


class ToolFailureTests(unittest.TestCase):
    """工具不可用/超时不能返回空 findings，否则门禁会 fail-open。"""

    def test_format_launch_failure_raises(self):
        workspace = Path(tempfile.mkdtemp())
        source = workspace / "a.c"
        source.write_text("int a;\n", encoding="utf-8")
        with mock.patch.object(codecheck_tools, "run_process", side_effect=OSError("cannot execute")):
            with self.assertRaises(codecheck_tools.ToolRunError):
                codecheck_tools.format_findings([source], "clang-format", "", workspace, 10)

    def test_format_timeout_raises(self):
        workspace = Path(tempfile.mkdtemp())
        source = workspace / "a.c"
        source.write_text("int a;\n", encoding="utf-8")
        with mock.patch.object(
            codecheck_tools,
            "run_process",
            side_effect=codecheck_tools.subprocess.TimeoutExpired("clang-format", 1),
        ):
            with self.assertRaises(codecheck_tools.ToolRunError):
                codecheck_tools.format_findings([source], "clang-format", "", workspace, 10)

    def test_tidy_launch_failure_raises(self):
        workspace = Path(tempfile.mkdtemp())
        source = workspace / "a.c"
        source.write_text("int a;\n", encoding="utf-8")
        with mock.patch.object(codecheck_tools, "run_process", side_effect=OSError("cannot execute")):
            with self.assertRaises(codecheck_tools.ToolRunError):
                codecheck_tools.tidy_findings(
                    [source],
                    "clang-tidy",
                    tidy_config_file="",
                    compile_db="",
                    include_dirs=[],
                    defines=[],
                    workspace=workspace,
                    timeout=10,
                )

    def test_tidy_abnormal_exit_without_findings_raises(self):
        workspace = Path(tempfile.mkdtemp())
        source = workspace / "a.c"
        source.write_text("int a;\n", encoding="utf-8")

        def fake_run(command, timeout):
            return codecheck_tools.subprocess.CompletedProcess(command, 2, "", "fatal error")

        with mock.patch.object(codecheck_tools, "run_process", side_effect=fake_run):
            with self.assertRaises(codecheck_tools.ToolRunError):
                codecheck_tools.tidy_findings(
                    [source],
                    "clang-tidy",
                    tidy_config_file="",
                    compile_db="",
                    include_dirs=[],
                    defines=[],
                    workspace=workspace,
                    timeout=10,
                )


class TidyCommandTests(unittest.TestCase):
    def test_relative_include_dirs_resolve_against_workspace(self):
        workspace = Path(tempfile.mkdtemp())
        source = workspace / "a.c"
        source.write_text("int a;\n", encoding="utf-8")
        captured = {}

        def fake_run(command, timeout):
            captured["command"] = command
            return codecheck_tools.subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(codecheck_tools, "run_process", side_effect=fake_run):
            codecheck_tools.tidy_findings(
                [source],
                "clang-tidy",
                tidy_config_file="",
                compile_db="",
                include_dirs=["APP"],
                defines=["USE_HAL_DRIVER"],
                workspace=workspace,
                timeout=10,
            )
        self.assertIn(f"-I{(workspace / 'APP').resolve()}", captured["command"])
        self.assertIn("-DUSE_HAL_DRIVER", captured["command"])


class LocalConfigTests(unittest.TestCase):
    def test_explicit_config_is_loaded_directly(self):
        path = Path(tempfile.mkdtemp()) / "custom.json"
        path.write_text('{"fail_on": "error", "timeout_seconds": 42}', encoding="utf-8")
        config = codecheck.load_effective_local_config(str(path))
        self.assertEqual(config["fail_on"], "error")
        self.assertEqual(config["timeout_seconds"], 42)

    def test_missing_explicit_config_raises(self):
        missing = Path(tempfile.mkdtemp()) / "missing.json"
        with self.assertRaises(codecheck_runtime.ConfigError):
            codecheck.load_effective_local_config(str(missing))


class MainToolFailureTests(unittest.TestCase):
    def test_tool_failure_is_reported_as_error(self):
        workspace = Path(tempfile.mkdtemp())
        (workspace / "a.c").write_text("int a;\n", encoding="utf-8")
        argv = [
            "codecheck.py",
            "format",
            "--workspace",
            str(workspace),
            "--json",
            "--clang-format-exe",
            "missing-tool",
        ]
        buffer = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            codecheck_tools, "run_process", side_effect=OSError("cannot execute")
        ), contextlib.redirect_stdout(buffer):
            exit_code = codecheck.main()
        self.assertEqual(exit_code, 1)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["error"]["code"], "tool_failed")


class DiscoveryTests(unittest.TestCase):
    def test_excludes_named_directories_and_non_sources(self):
        workspace = Path(tempfile.mkdtemp())
        (workspace / "APP").mkdir()
        (workspace / "LIB").mkdir()
        (workspace / "build").mkdir()
        (workspace / "APP" / "app.c").write_text("int a;\n", encoding="utf-8")
        (workspace / "APP" / "app.h").write_text("#pragma once\n", encoding="utf-8")
        (workspace / "LIB" / "lib.c").write_text("int b;\n", encoding="utf-8")
        (workspace / "build" / "out.c").write_text("int c;\n", encoding="utf-8")
        (workspace / "README.md").write_text("x\n", encoding="utf-8")

        files, notes = codecheck_tools.discover_sources(
            workspace, source_roots=["."], exclude_names=["LIB"], exclude_paths=["build"]
        )
        self.assertEqual(sorted(path.name for path in files), ["app.c", "app.h"])
        self.assertEqual(notes, [])


class RuntimeConfigTests(unittest.TestCase):
    def test_corrupt_json_raises_instead_of_returning_empty_dict(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(codecheck_runtime.ConfigError):
            codecheck_runtime.load_json_file(path)

    def test_missing_json_returns_empty_dict(self):
        path = Path(tempfile.mkdtemp()) / "missing.json"
        self.assertEqual(codecheck_runtime.load_json_file(path), {})

    def test_atomic_write_roundtrip(self):
        path = Path(tempfile.mkdtemp()) / "state.json"
        codecheck_runtime.save_json_file(path, {"last_check": {"passed": True}})
        self.assertEqual(codecheck_runtime.load_json_file(path), {"last_check": {"passed": True}})
        leftovers = [item for item in path.parent.iterdir() if item.name.startswith(".state.json.")]
        self.assertEqual(leftovers, [])


class ResolverTests(unittest.TestCase):
    def test_cli_value_wins_over_path(self):
        value, source = codecheck_runtime.resolve_tool_param(
            "clang_format_exe",
            "C:/tools/clang-format.exe",
            local_config={"clang_format_exe": "C:/other.exe"},
            local_keys=["clang_format_exe"],
            path_candidates=["clang-format"],
        )
        self.assertTrue(value.endswith("clang-format.exe"))
        self.assertEqual(source, "cli")

    def test_path_source_is_recorded(self):
        with mock.patch.object(codecheck_runtime.shutil, "which", return_value="C:/llvm/bin/clang-format.exe"):
            value, source = codecheck_runtime.resolve_tool_param(
                "clang_format_exe",
                None,
                local_config={},
                local_keys=["clang_format_exe"],
                path_candidates=["clang-format.exe"],
            )
        self.assertTrue(value.endswith("clang-format.exe"))
        self.assertEqual(source, "path:clang-format.exe")

    def test_missing_tool_raises_when_required(self):
        with mock.patch.object(codecheck_runtime.shutil, "which", return_value=None):
            with self.assertRaises(codecheck_runtime.ConfigError):
                codecheck_runtime.resolve_tool_param(
                    "clang_tidy_exe",
                    None,
                    local_config={},
                    local_keys=["clang_tidy_exe"],
                    path_candidates=["clang-tidy"],
                    required=True,
                )

    def test_workspace_relative_path_resolves_against_workspace(self):
        workspace = Path(tempfile.mkdtemp())
        resolved = codecheck_tools.resolve_workspace_path(".clang-format", workspace)
        self.assertEqual(Path(resolved), (workspace / ".clang-format").resolve())


class RulesTests(unittest.TestCase):
    def test_rules_library_loads(self):
        skill_directory = Path(__file__).resolve().parents[1]
        rules = codecheck_tools.load_rules(skill_directory)
        self.assertGreaterEqual(len(rules), 14)
        self.assertTrue(all("id" in rule and "title" in rule for rule in rules))


class ResultTests(unittest.TestCase):
    def test_result_envelope(self):
        result = codecheck_runtime.make_result(
            status="ok",
            action="check",
            summary="ok",
            details={"gate": {"passed": True}},
            context={"provider": "codecheck"},
            metrics={"files": 1},
            timing={"elapsed_ms": 1},
        )
        for key in ("status", "action", "summary", "details", "context", "metrics", "timing"):
            self.assertIn(key, result)
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)