"""Command-line interface for LeanProbe."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import __version__
from .benchmark import (
    _external_command_specs,
    run_benchmark,
    run_benchmark_suite,
    run_file_level_benchmark,
)
from .core import LeanProbe


def _read_text_arg(value: str, file_value: str) -> str:
    if value and file_value:
        raise SystemExit("Use either --replacement or --replacement-file, not both.")
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    return value


def _emit(payload: dict[str, Any], *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _package_version() -> str:
    try:
        return version("lean-probe")
    except PackageNotFoundError:
        return __version__


def _probe_from_args(args: argparse.Namespace) -> LeanProbe:
    return LeanProbe(
        auto_build=bool(getattr(args, "auto_build", False)),
        local_repl_path=getattr(args, "local_repl_path", "") or None,
        lake_path=getattr(args, "lake_path", "lake") or "lake",
        verbose=bool(getattr(args, "verbose", False)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lean-probe", description="Fast Lean 4 proof feedback for agents.")
    parser.add_argument("--version", action="version", version=f"lean-probe {_package_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument("--cwd", default="", help="Lean project working directory")
    common_parent.add_argument("--timeout-s", type=int, default=60, help="LeanInteract request timeout")
    common_parent.add_argument("--auto-build", action="store_true", help="Let LeanInteract build the Lean project")
    common_parent.add_argument("--local-repl-path", default="", help="Use a specific local Lean REPL checkout")
    common_parent.add_argument("--lake-path", default="lake", help="Path to lake executable")
    common_parent.add_argument("--verbose", action="store_true", help="Enable LeanInteract verbose setup")
    common_parent.add_argument("--pretty", action="store_true", help="Pretty-print JSON")

    prepare = sub.add_parser("prepare", parents=[common_parent], help="Warm imports and optional prior declarations")
    prepare.add_argument("file_path")
    prepare.add_argument("--theorem-id", default="")

    check = sub.add_parser("check", parents=[common_parent], help="Check one target declaration")
    check.add_argument("file_path")
    check.add_argument("theorem_id")
    check.add_argument("--replacement", default="")
    check.add_argument("--replacement-file", default="")
    check.add_argument("--include-tactics", action="store_true")

    feedback = sub.add_parser("feedback", parents=[common_parent], help="Return rich target feedback")
    feedback.add_argument("file_path")
    feedback.add_argument("theorem_id")
    feedback.add_argument("--replacement", default="")
    feedback.add_argument("--replacement-file", default="")

    state = sub.add_parser("state", parents=[common_parent], help="Create a proof state from Lean code")
    state.add_argument("--code", default="")
    state.add_argument("--code-file", default="")
    state.add_argument("--include-tactics", action="store_true")

    tactic_script = sub.add_parser(
        "tactic-script", parents=[common_parent], help="Run tactics against a code snippet with sorry"
    )
    tactic_script.add_argument("--code", default="")
    tactic_script.add_argument("--code-file", default="")
    tactic_script.add_argument("--tactic", action="append", default=[], help="Tactic to apply in order")

    benchmark = sub.add_parser("benchmark", parents=[common_parent], help="Compare Lake and warm LeanProbe checks")
    benchmark.add_argument("file_path")
    benchmark.add_argument("theorem_id")
    benchmark.add_argument("--replacement", default="")
    benchmark.add_argument("--replacement-file", default="")
    benchmark.add_argument("--runs", type=int, default=5)
    benchmark.add_argument("--warmups", type=int, default=1)
    benchmark.add_argument("--include-feedback", action="store_true")
    benchmark.add_argument(
        "--include-no-cache", action="store_true", help="Time fresh-server LeanProbe checks with no cache reuse"
    )
    benchmark.add_argument(
        "--external-command",
        action="append",
        default=[],
        help="Additional verifier timing as NAME=COMMAND; placeholders: {file}, {original}, {cwd}, {theorem}",
    )
    benchmark.add_argument("--results-dir", default="", help="Optional directory for raw JSON output")
    benchmark.add_argument("--label", default="", help="Optional benchmark label")

    suite = sub.add_parser("benchmark-suite", parents=[common_parent], help="Run a JSON benchmark case suite")
    suite.add_argument("--cases-file", required=True, help="JSON file listing benchmark targets")
    suite.add_argument("--runs", type=int, default=5)
    suite.add_argument("--warmups", type=int, default=1)
    suite.add_argument("--include-feedback", action="store_true")
    suite.add_argument(
        "--include-no-cache", action="store_true", help="Time fresh-server LeanProbe checks with no cache reuse"
    )
    suite.add_argument(
        "--external-command",
        action="append",
        default=[],
        help="Additional verifier timing as NAME=COMMAND; placeholders: {file}, {original}, {cwd}, {theorem}",
    )
    suite.add_argument("--results-dir", default="", help="Optional directory for raw JSON output")
    suite.add_argument("--case", action="append", default=[], help="Run only a named benchmark case")

    file_benchmark = sub.add_parser(
        "benchmark-file",
        parents=[common_parent],
        help="Compare repeated same-file declaration checks with LeanInteract env reuse",
    )
    file_benchmark.add_argument("file_path")
    file_benchmark.add_argument("--runs", type=int, default=3)
    file_benchmark.add_argument(
        "--max-declarations",
        dest="max_declarations",
        type=int,
        default=0,
        help="Limit declarations; 0 means all",
    )
    file_benchmark.add_argument("--max-cutoffs", dest="max_declarations", type=int, help=argparse.SUPPRESS)
    file_benchmark.add_argument("--skip-no-cache", action="store_true", help="Skip fresh-server no-cache comparison")
    file_benchmark.add_argument(
        "--external-command",
        action="append",
        default=[],
        help="Additional full-file scenario verifier as NAME=COMMAND; placeholders: {file}, {original}, {cwd}, {theorem}",
    )
    file_benchmark.add_argument("--results-dir", default="", help="Optional directory for raw JSON output")
    file_benchmark.add_argument("--label", default="", help="Optional benchmark label")

    sub.add_parser("mcp", help="Run the LeanProbe MCP stdio server")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "mcp":
        from .mcp_server import run

        try:
            run()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    if args.command == "benchmark":
        replacement = _read_text_arg(args.replacement, args.replacement_file)
        try:
            external_commands = _external_command_specs(args.external_command)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        payload = run_benchmark(
            file_path=args.file_path,
            theorem_id=args.theorem_id,
            cwd=args.cwd or None,
            replacement=replacement,
            runs=args.runs,
            warmups=args.warmups,
            include_feedback=args.include_feedback,
            timeout_s=args.timeout_s,
            auto_build=args.auto_build,
            local_repl_path=args.local_repl_path or None,
            lake_path=args.lake_path,
            verbose=args.verbose,
            include_no_cache=args.include_no_cache,
            external_commands=external_commands,
            results_dir=args.results_dir or None,
            label=args.label,
        )
        _emit(payload, pretty=args.pretty)
        return 0 if payload.get("success") else 1

    if args.command == "benchmark-suite":
        try:
            external_commands = _external_command_specs(args.external_command)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        payload = run_benchmark_suite(
            cases_file=args.cases_file,
            cwd=args.cwd or None,
            runs=args.runs,
            warmups=args.warmups,
            include_feedback=args.include_feedback,
            timeout_s=args.timeout_s,
            auto_build=args.auto_build,
            local_repl_path=args.local_repl_path or None,
            lake_path=args.lake_path,
            verbose=args.verbose,
            include_no_cache=args.include_no_cache,
            external_commands=external_commands,
            results_dir=args.results_dir or None,
            case_labels=args.case or None,
        )
        _emit(payload, pretty=args.pretty)
        return 0 if payload.get("success") else 1

    if args.command == "benchmark-file":
        try:
            external_commands = _external_command_specs(args.external_command)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        payload = run_file_level_benchmark(
            file_path=args.file_path,
            cwd=args.cwd or None,
            runs=args.runs,
            max_cutoffs=args.max_declarations,
            timeout_s=args.timeout_s,
            auto_build=args.auto_build,
            local_repl_path=args.local_repl_path or None,
            lake_path=args.lake_path,
            verbose=args.verbose,
            include_no_cache=not args.skip_no_cache,
            external_commands=external_commands,
            results_dir=args.results_dir or None,
            label=args.label,
        )
        _emit(payload, pretty=args.pretty)
        return 0 if payload.get("success") else 1

    probe = _probe_from_args(args)
    try:
        if args.command == "prepare":
            payload = probe.prepare_file(
                args.file_path,
                theorem_id=args.theorem_id,
                cwd=args.cwd or None,
                timeout_s=args.timeout_s,
            )
        elif args.command == "check":
            payload = probe.check_target(
                args.file_path,
                theorem_id=args.theorem_id,
                cwd=args.cwd or None,
                replacement=_read_text_arg(args.replacement, args.replacement_file),
                include_tactics=args.include_tactics,
                timeout_s=args.timeout_s,
            )
        elif args.command == "feedback":
            payload = probe.feedback(
                args.file_path,
                theorem_id=args.theorem_id,
                cwd=args.cwd or None,
                replacement=_read_text_arg(args.replacement, args.replacement_file),
                timeout_s=args.timeout_s,
            )
        elif args.command == "state":
            code = _read_text_arg(args.code, args.code_file)
            if not code:
                code = sys.stdin.read()
            payload = probe.proof_state_from_code(
                code,
                cwd=args.cwd or None,
                include_tactics=args.include_tactics,
                timeout_s=args.timeout_s,
            )
        elif args.command == "tactic-script":
            code = _read_text_arg(args.code, args.code_file)
            if not code:
                code = sys.stdin.read()
            payload = probe.proof_state_from_code(code, cwd=args.cwd or None, timeout_s=args.timeout_s)
            steps = []
            current = None
            if payload.get("sorries"):
                current = payload["sorries"][0].get("proof_state")
            for tactic in args.tactic:
                if current is None:
                    break
                step = probe.tactic_step(payload["session_id"], int(current), tactic, timeout_s=args.timeout_s)
                steps.append(step)
                current = step.get("proof_state")
            payload["steps"] = steps
        else:
            raise SystemExit(f"Unknown command: {args.command}")
    finally:
        probe.close()

    _emit(payload, pretty=bool(getattr(args, "pretty", False)))
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
