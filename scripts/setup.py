"""BuildArena one-command setup. All game-side work is scripted.

The only expected human stops are missing DLC content and a missing Workshop
subscription for BuildArena ToolKit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONTROL_ROOT = _REPO_ROOT / "control"
for candidate in (_REPO_ROOT, _CONTROL_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from besiege_cli.bootstrap import (
    STATUS_BLOCKED,
    STATUS_PASSED,
    report_exit_code,
    run_bootstrap,
)


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _say(*, zh: str, en: str) -> None:
    print(zh)
    print(en)


def _print_report(report) -> None:
    payload = report.to_dict()
    print("")
    print("─" * 64)
    print(f"  setup status: {payload['status']}")
    print(f"  toolkit_source: {payload['toolkit_source']}")
    if payload.get("unverified_distribution"):
        print(f"  unverified_distribution: {payload['unverified_distribution']}")
    print("─" * 64)
    for stage in payload["stages"]:
        print(f"[{stage['status']}] {stage['name']}: {stage['message']}")
    print("")
    if report.status == STATUS_PASSED:
        _say(
            zh="配置完成。MCP 已写入 mcp.json，指南针、全块遥测冒烟和 Reusable_Heavy_Launcher 的 LONE ORB 入轨演示都已通过。",
            en="Setup finished. mcp.json is written; compass, all-block telemetry smoke, and the Reusable_Heavy_Launcher LONE ORB orbit demo passed.",
        )
        return
    if report.status == STATUS_BLOCKED:
        last = payload["stages"][-1]["message"] if payload["stages"] else ""
        _say(
            zh=f"需要你处理购买/订阅后重跑：{last}",
            en=f"A human entitlement step is required, then re-run: {last}",
        )
        return
    last = payload["stages"][-1]["message"] if payload["stages"] else ""
    _say(
        zh=f"自动配置失败：{last}",
        en=f"Automated setup failed: {last}",
    )


def run(
    *,
    besiege_data_override: str | None,
    interactive: bool,
) -> int:
    _configure_utf8_output()
    report = run_bootstrap(
        repo_root=_REPO_ROOT,
        besiege_data_override=besiege_data_override,
        interactive=interactive,
    )
    _print_report(report)
    report_path = _REPO_ROOT / ".local" / "setup-report.json"
    if report_path.is_file():
        print(f"setup-report: {report_path}")
    from buildarena.paths import check_environment, format_environment_report

    print("")
    print(format_environment_report(results=check_environment()))
    return report_exit_code(report)


def main() -> int:
    parser = argparse.ArgumentParser(description="BuildArena one-command setup / BuildArena 一键配置")
    parser.add_argument("--besiege-data", default=None, help="Besiege_Data path when auto-detection fails.")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not open a Workshop page or prompt for paths.",
    )
    args = parser.parse_args()
    return run(
        besiege_data_override=args.besiege_data,
        interactive=not args.non_interactive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
