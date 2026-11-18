#!/usr/bin/env python3
"""verify 入口：host 单元测试、串口黑盒冒烟与测试报告。

动作：
- scan   发现测试计划、单测框架、串口与最近构建/烧录状态
- unit   执行 host 单元测试并解析 JUnit
- smoke  按计划执行串口黑盒冒烟
- run    依次执行计划中的 unit 与 smoke（任一失败即失败）
- report 输出最近一次测试报告

退出码：0 通过，1 失败/基础设施错误，130 中断。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from verify_plan import PlanError, describe_plan, load_plan
from verify_runners import RunnerError, StepFailure, execute_smoke, run_unit, scan_workspace
from verify_runtime import (
    ConfigError,
    load_json_file,
    make_result,
    make_timing,
    now_iso,
    output_json,
    parameter_context,
    report_dir,
    resolve_plan_path,
    save_json_file,
    update_state_entry,
    workspace_root,
    write_report,
)


class Parser(argparse.ArgumentParser):
    """把 argparse 的退出行为改成可捕获的 ValueError，便于统一输出 JSON。"""

    def error(self, message: str) -> None:
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="verify",
        description="执行 host 单元测试与串口黑盒冒烟，输出 JUnit/JSON 证据并写回 workflow 状态。",
    )
    parser.add_argument("action", choices=["scan", "unit", "smoke", "run", "report"])
    parser.add_argument("--workspace", default=".", help="工程根目录，默认当前目录")
    parser.add_argument("--plan", help="测试计划路径；默认读取 verify.plan 或约定位置")
    parser.add_argument("--json", action="store_true", help="输出统一 JSON 结果")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印将要执行的步骤，不访问硬件")
    parser.add_argument("--case", help="只执行指定名称的冒烟用例")
    parser.add_argument("--junit", help="覆盖 JUnit XML 输出路径")
    parser.add_argument("--port", help="覆盖串口")
    parser.add_argument("--baudrate", type=int, help="覆盖串口波特率")
    parser.add_argument("--report", help="report 动作读取的报告路径，默认 latest.json")
    return parser


def resolve_plan(workspace: Path, explicit: str | None) -> tuple[Path, dict]:
    plan_path = resolve_plan_path(str(workspace), explicit)
    if plan_path is None:
        raise ConfigError(
            "未找到测试计划：请用 --plan 指定，或创建 .embeddedskills/verify/plan.json"
        )
    if not plan_path.is_file():
        raise ConfigError(f"测试计划不存在：{plan_path}")
    return plan_path, load_plan(plan_path)


def _unit_summary(unit: dict) -> str:
    totals = unit.get("totals") or {}
    has_junit = bool((unit.get("junit") or {}).get("available"))
    if unit.get("dry_run"):
        return f"unit 预演：{unit.get('framework')} {' '.join(unit.get('command') or [])}"
    if unit.get("passed"):
        if not has_junit:
            return "unit 通过：命令退出码 0（未生成 JUnit 报告）"
        return f"unit 通过：{totals.get('tests', 0)} 个用例，{totals.get('skipped', 0)} 跳过"
    if not has_junit:
        return f"unit 失败：命令退出码 {unit.get('exit_code')}（未生成 JUnit 报告）"
    return (
        f"unit 失败：{totals.get('tests', 0)} 个用例，"
        f"{totals.get('failed', 0)} 失败，{totals.get('errors', 0)} 错误"
    )


def _smoke_summary(smoke: dict) -> str:
    if smoke.get("dry_run"):
        return f"smoke 预演：{len(smoke.get('cases') or [])} 个用例"
    return (
        f"smoke {'通过' if smoke.get('passed') else '失败'}："
        f"{smoke.get('passed_cases', 0)}/{smoke.get('total', 0)} 个用例通过"
    )


def _write_evidence(
    *,
    action: str,
    result: dict,
    workspace: Path,
    passed: bool | None,
    totals: dict,
) -> dict:
    """写报告、回写 last_verify，并把路径补进结果。"""
    paths = write_report(str(workspace), result, prefix=action)
    state_record = {
        "action": action,
        "passed": passed,
        "totals": totals,
        "report": paths["report"],
        "latest_report": paths["latest_report"],
        "error_code": (result.get("error") or {}).get("code", ""),
    }
    state_info = update_state_entry("last_verify", state_record, str(workspace))
    result["state"] = state_info
    result["artifacts"] = paths
    save_json_file(paths["report"], result)
    save_json_file(paths["latest_report"], result)
    return result


def run_action(args: argparse.Namespace, workspace: Path, started_at: str, started: float) -> dict:
    action = args.action

    if action == "scan":
        scan = scan_workspace(str(workspace))
        plan_text = scan.get("plan") or "未发现"
        summary = f"plan={plan_text}；单测框架={scan.get('unit_frameworks') or '未发现'}"
        return make_result(
            status="ok",
            action="scan",
            summary=summary,
            details=scan,
            context=parameter_context(provider="verify", workspace=str(workspace), plan_path=scan.get("plan")),
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
        )

    if action == "report":
        report_path = Path(args.report) if args.report else report_dir(str(workspace)) / "latest.json"
        if not report_path.is_absolute():
            report_path = workspace / report_path
        if not report_path.is_file():
            raise ConfigError(f"报告不存在：{report_path}")
        report = load_json_file(report_path, required=True)
        return make_result(
            status="ok",
            action="report",
            summary=f"读取报告：{report_path}",
            details=report,
            artifacts={"report": str(report_path.resolve())},
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
        )

    plan_path, plan = resolve_plan(workspace, args.plan)
    plan_info = describe_plan(plan)
    context = parameter_context(
        provider="verify",
        workspace=str(workspace),
        plan_path=str(plan_path),
    )
    details: dict = {"plan": plan_info}
    metrics: dict = {}
    passed: bool | None = None
    error: dict | None = None

    # 空对象表示“该段存在但使用自动探测”，不能用 bool() 判断，否则会静默跳过测试段。
    unit_configured = "unit" in plan and plan.get("unit") is not None
    smoke_configured = "smoke" in plan and plan.get("smoke") is not None

    if action == "unit":
        if not unit_configured:
            raise PlanError("测试计划未配置 unit 段")
        unit = run_unit(plan, str(workspace), cli_junit=args.junit, dry_run=args.dry_run)
        details["unit"] = unit
        summary = _unit_summary(unit)
        if args.dry_run:
            passed = None
        else:
            passed = bool(unit.get("passed"))
            if not passed:
                error = unit.get("error") or {"code": "unit_failed", "message": summary}
            metrics = {"tests": (unit.get("totals") or {}).get("tests", 0), **(unit.get("totals") or {})}

    elif action == "smoke":
        if not smoke_configured:
            raise PlanError("测试计划未配置 smoke 段")
        smoke = execute_smoke(
            plan,
            str(workspace),
            only_case=args.case,
            dry_run=args.dry_run,
            cli_port=args.port,
            cli_baudrate=args.baudrate,
        )
        details["smoke"] = smoke
        summary = _smoke_summary(smoke)
        if args.dry_run:
            passed = None
        else:
            passed = bool(smoke.get("passed"))
            if not passed:
                error = {
                    "code": "test_failed",
                    "message": f"{smoke.get('failed_cases', 0)} 个冒烟用例失败",
                }
            metrics = {
                "total": smoke.get("total", 0),
                "passed_cases": smoke.get("passed_cases", 0),
                "failed_cases": smoke.get("failed_cases", 0),
            }

    elif action == "run":
        unit = None
        smoke = None
        if unit_configured:
            unit = run_unit(plan, str(workspace), cli_junit=args.junit, dry_run=args.dry_run)
            details["unit"] = unit
        if smoke_configured and (unit is None or args.dry_run or unit.get("passed")):
            smoke = execute_smoke(
                plan,
                str(workspace),
                only_case=args.case,
                dry_run=args.dry_run,
                cli_port=args.port,
                cli_baudrate=args.baudrate,
            )
            details["smoke"] = smoke
        elif smoke_configured:
            details["smoke"] = {"skipped": True, "reason": "unit 失败，未执行冒烟"}

        if args.dry_run:
            passed = None
            summary = "run 预演：unit 与 smoke 已校验"
        else:
            unit_ok = unit is None or bool(unit.get("passed"))
            smoke_ok = smoke is None or bool(smoke.get("passed"))
            passed = unit_ok and smoke_ok
            if not unit_ok:
                error = (unit or {}).get("error") or {"code": "unit_failed", "message": "unit 测试失败"}
            elif not smoke_ok:
                error = {
                    "code": "test_failed",
                    "message": f"{smoke.get('failed_cases', 0)} 个冒烟用例失败",
                }
            parts = []
            if unit is not None:
                parts.append(_unit_summary(unit))
            if smoke is not None:
                parts.append(_smoke_summary(smoke))
            summary = "；".join(parts) if parts else "没有可执行的测试段"
            metrics = {
                "unit": (unit or {}).get("totals", {}),
                "smoke": {
                    "total": (smoke or {}).get("total", 0),
                    "passed_cases": (smoke or {}).get("passed_cases", 0),
                    "failed_cases": (smoke or {}).get("failed_cases", 0),
                },
            }
    else:  # pragma: no cover - argparse 已限制
        raise ConfigError(f"不支持的动作：{action}")

    status = "ok" if passed or (args.dry_run and passed is None) else "error"
    result = make_result(
        status=status,
        action=action,
        summary=summary,
        details=details,
        context=context,
        metrics=metrics,
        timing=make_timing(started_at, (time.monotonic() - started) * 1000),
        error=error if not args.dry_run else None,
    )

    if not args.dry_run:
        _write_evidence(
            action=action,
            result=result,
            workspace=workspace,
            passed=passed,
            totals=metrics if isinstance(metrics, dict) else {},
        )
    return result


def print_human(result: dict) -> None:
    status = result.get("status", "")
    print(f"[verify] {result.get('action', '')}: {status} - {result.get('summary', '')}")
    details = result.get("details") or {}
    unit = details.get("unit") or {}
    smoke = details.get("smoke") or {}
    if unit:
        if unit.get("dry_run"):
            print(f"  unit: {' '.join(unit.get('command') or [])} (cwd={unit.get('working_dir')})")
        else:
            print(
                f"  unit: {unit.get('totals', {}).get('tests', 0)} tests, "
                f"{unit.get('totals', {}).get('failed', 0)} failed, "
                f"{unit.get('totals', {}).get('errors', 0)} errors, {unit.get('duration_ms', 0)}ms"
            )
            if unit.get("junit", {}).get("available"):
                print(f"  junit: {unit['junit'].get('path')}")
    if smoke:
        if smoke.get("skipped"):
            print(f"  smoke: skipped ({smoke.get('reason')})")
        else:
            for case in smoke.get("cases") or []:
                if case.get("dry_run"):
                    continue
                mark = "PASS" if case.get("status") == "passed" else "FAIL"
                print(f"  [{mark}] {case.get('name')} ({case.get('duration_ms', 0)}ms)")
                if case.get("error"):
                    print(f"         {case['error']}")
    if result.get("artifacts", {}).get("report"):
        print(f"  report: {result['artifacts']['report']}")
    error = result.get("error") or {}
    if error:
        print(f"  error: {error.get('code')}: {error.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(raw_args)
    except ValueError as exc:
        result = make_result(
            status="error",
            action="verify",
            summary=f"参数错误：{exc}",
            details={},
            error={"code": "invalid_arguments", "message": str(exc)},
        )
        if "--json" in raw_args:
            output_json(result)
        else:
            parser.print_usage(sys.stderr)
            print(f"verify: error: {exc}", file=sys.stderr)
        return 1
    workspace = workspace_root(args.workspace)
    started_at = now_iso()
    started = time.monotonic()
    try:
        result = run_action(args, workspace, started_at, started)
    except KeyboardInterrupt:
        result = make_result(
            status="error",
            action=args.action,
            summary="已中断",
            context=parameter_context(provider="verify", workspace=str(workspace)),
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
            error={"code": "interrupted", "message": "用户中断"},
        )
        if args.json:
            output_json(result)
        else:
            print_human(result)
        return 130
    except (ConfigError, PlanError) as exc:
        code = "config_invalid" if isinstance(exc, ConfigError) else "plan_invalid"
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            context=parameter_context(provider="verify", workspace=str(workspace)),
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
            error={"code": code, "message": str(exc)},
        )
    except RunnerError as exc:
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            context=parameter_context(provider="verify", workspace=str(workspace)),
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
            error={"code": exc.code, "message": str(exc)},
        )
    except StepFailure as exc:  # 理论上 execute_smoke 内部会处理
        result = make_result(
            status="error",
            action=args.action,
            summary=str(exc),
            context=parameter_context(provider="verify", workspace=str(workspace)),
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
            error={"code": "test_failed", "message": str(exc)},
        )
    except Exception as exc:  # 避免把 traceback 当 JSON 输出
        result = make_result(
            status="error",
            action=args.action,
            summary=f"内部错误：{exc}",
            context=parameter_context(provider="verify", workspace=str(workspace)),
            details={"exception": type(exc).__name__},
            timing=make_timing(started_at, (time.monotonic() - started) * 1000),
            error={"code": "internal_error", "message": str(exc)},
        )

    if args.json:
        output_json(result)
    else:
        print_human(result)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())