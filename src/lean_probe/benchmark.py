"""Benchmark helpers for LeanProbe.

The benchmark harness is intentionally standalone. It compares LeanProbe
against canonical terminal Lean checks and against LeanProbe itself with cache
reuse disabled; it does not import or require any external project-specific
tooling.
"""

from __future__ import annotations

import json
import math
import platform
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .core import LeanProbe, find_lean_project_root, segment_file


AMORTIZED_ATTEMPTS = (1, 3, 10)


@dataclass(frozen=True)
class BenchmarkCase:
    """One declaration target for the benchmark suite."""

    label: str
    file_path: str
    theorem_id: str
    group: str = ""
    size: str = ""
    description: str = ""


def _summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"runs": 0, "min": 0.0, "p50": 0.0, "mean": 0.0, "max": 0.0}
    return {
        "runs": len(values),
        "min": round(min(values), 3),
        "p50": round(statistics.median(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "max": round(max(values), 3),
    }


def _platform_payload() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
    }


def _run_text_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    env: dict[str, str] | None = None,
) -> tuple[bool, float, str]:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
        elapsed = time.perf_counter() - start
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode == 0, elapsed, output
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        output = (stdout + "\n" + stderr).strip()
        detail = f"timed out after {timeout_s}s"
        return False, elapsed, (output + "\n" + detail).strip()


def _has_hard_lean_error(output: str) -> bool:
    for line in str(output or "").splitlines():
        lowered = line.lower()
        if ": error:" in lowered or lowered.startswith("error:"):
            return True
    return False


def _last_json_object(output: str) -> dict[str, Any] | None:
    for line in reversed(str(output or "").splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            continue
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def _resolve_project_file(project_root: Path, file_path: str | Path) -> Path:
    path = Path(file_path).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _run_lake_check(
    project_root: Path,
    file_path: Path,
    timeout_s: int,
    lake_path: str | Path = "lake",
) -> tuple[bool, float, str]:
    try:
        relative = file_path.relative_to(project_root)
    except ValueError:
        relative = file_path
    ok, elapsed, output = _run_text_command(
        [str(lake_path), "env", "lean", str(relative)],
        cwd=project_root,
        timeout_s=timeout_s,
    )
    return ok, elapsed, output[-4000:]


def _run_external_command(
    template: str,
    *,
    project_root: Path,
    original_file: Path,
    lake_target: Path,
    theorem_id: str,
    timeout_s: int,
) -> tuple[bool, float, str]:
    command = template.format(
        file=str(lake_target),
        original=str(original_file),
        cwd=str(project_root),
        theorem=theorem_id,
    )
    ok, elapsed, output = _run_text_command(
        ["/bin/sh", "-lc", command],
        cwd=project_root,
        timeout_s=timeout_s,
    )
    return ok, elapsed, output[-4000:]


def _response_ok(response: Any) -> bool:
    if response is None:
        return False
    has_errors = bool(response.has_errors()) if hasattr(response, "has_errors") else False
    valid = bool(response.lean_code_is_valid(allow_sorry=False)) if hasattr(response, "lean_code_is_valid") else True
    return valid and not has_errors


def _methodology_payload(
    *,
    project_root: Path,
    file_path: Path,
    theorem_id: str,
    lake_target: Path | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "lean_file": str(file_path),
        "target_declaration": theorem_id,
        "project_root": str(project_root),
        "surfaces": {
            "terminal_lake_env_lean": "run `lake env lean <temp full file>` from the Lean project root",
            "lean_probe_prepare": "start LeanInteract, elaborate header/imports and prior declarations before target",
            "lean_probe_check": "check only the target declaration replacement against cached env_before_target",
            "lean_probe_feedback": "same target check with LeanInteract tactic/proof-state metadata enabled",
            "lean_probe_no_cache_check": "fresh LeanProbe/LeanInteract server per attempt; no cross-attempt cache reuse",
        },
        "acceptance_policy": {
            "lake": "process exit code 0; warnings accepted",
            "lean_probe": "LeanInteract response valid without sorry and no hard errors",
        },
    }
    if lake_target is not None:
        payload["lake_temp_file"] = str(lake_target)
    return payload


def _lake_target_with_replacement(
    original: Path,
    theorem_id: str,
    replacement: str,
) -> tuple[Path, Path | None, str]:
    if not replacement:
        return original, None, ""

    text = original.read_text(encoding="utf-8")
    _header, segments = segment_file(text)
    short = theorem_id.split(".")[-1]
    target = next((segment for segment in segments if segment.name in {theorem_id, short}), None)
    if target is None:
        return original, None, "target declaration not found; Lake benchmark used original file"

    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(original.parent),
        prefix=".lean_probe_bench_",
        suffix=".lean",
        delete=False,
    )
    try:
        tmp.write(text[: target.start])
        tmp.write(replacement.rstrip() + "\n")
        tmp.write(text[target.end :])
    finally:
        tmp.close()
    tmp_path = Path(tmp.name)
    return tmp_path, tmp_path, ""


def _target_replacement(file_path: Path, theorem_id: str) -> tuple[str, str]:
    text = file_path.read_text(encoding="utf-8")
    _header, segments = segment_file(text)
    short = theorem_id.split(".")[-1]
    target = next((segment for segment in segments if segment.name in {theorem_id, short}), None)
    if target is None:
        return "", "target declaration not found; benchmark used current file text"
    return target.text, ""


def _break_even_attempts(*, prepare_s: float, lake_p50: float, check_p50: float) -> int | None:
    if lake_p50 <= 0 or check_p50 <= 0 or lake_p50 <= check_p50:
        return None
    return max(1, int(math.ceil(prepare_s / (lake_p50 - check_p50))))


def _amortized_speedups(*, prepare_s: float, lake_p50: float, check_p50: float) -> dict[str, float]:
    speedups: dict[str, float] = {}
    for attempts in AMORTIZED_ATTEMPTS:
        lake_total = attempts * lake_p50
        probe_total = prepare_s + attempts * check_p50
        speedups[str(attempts)] = round(lake_total / probe_total, 2) if probe_total > 0 else 0.0
    return speedups


def _run_no_cache_probe_check(
    *,
    file_path: Path,
    theorem_id: str,
    project_root: Path,
    replacement: str,
    timeout_s: int,
    auto_build: bool,
    local_repl_path: str | Path | None,
    lake_path: str | Path,
    verbose: bool,
) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    probe = LeanProbe(
        auto_build=auto_build,
        local_repl_path=local_repl_path,
        lake_path=lake_path,
        verbose=verbose,
    )
    try:
        payload = probe.check_target(
            file_path,
            theorem_id=theorem_id,
            cwd=project_root,
            replacement=replacement,
            timeout_s=timeout_s,
        )
    finally:
        probe.close()
    return payload, time.perf_counter() - start


def _write_result_json(result: dict[str, Any], results_dir: str | Path | None, stem: str) -> str:
    if not results_dir:
        return ""
    directory = Path(results_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def _external_command_specs(specs: list[str]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for spec in specs:
        name, sep, command = str(spec).partition("=")
        if not sep or not name.strip() or not command.strip():
            raise ValueError("--external-command must use NAME=COMMAND")
        commands[name.strip()] = command.strip()
    return commands


def run_benchmark(
    *,
    file_path: str | Path,
    theorem_id: str,
    cwd: str | Path | None = None,
    replacement: str = "",
    runs: int = 5,
    warmups: int = 1,
    include_feedback: bool = False,
    timeout_s: int = 120,
    auto_build: bool = False,
    local_repl_path: str | Path | None = None,
    lake_path: str | Path = "lake",
    verbose: bool = False,
    include_no_cache: bool = False,
    external_commands: Mapping[str, str] | None = None,
    results_dir: str | Path | None = None,
    label: str = "",
) -> dict[str, Any]:
    project_root = find_lean_project_root(cwd or file_path)
    if project_root is None:
        return {"success": False, "error": "Lean project root not detected"}
    resolved = _resolve_project_file(project_root, file_path)
    if not resolved.is_file():
        return {"success": False, "error": f"Lean file not found: {resolved}"}

    replacement_warning = ""
    if not replacement:
        replacement, replacement_warning = _target_replacement(resolved, theorem_id)
    lake_target, cleanup_path, lake_target_warning = _lake_target_with_replacement(resolved, theorem_id, replacement)

    lake_times: list[float] = []
    probe_times: list[float] = []
    feedback_times: list[float] = []
    no_cache_times: list[float] = []
    external = dict(external_commands or {})
    external_times: dict[str, list[float]] = {name: [] for name in external}
    failures: list[dict[str, str]] = []
    prepare_elapsed = 0.0

    probe: LeanProbe | None = None
    try:
        for _ in range(max(0, warmups)):
            _run_lake_check(project_root, lake_target, timeout_s, lake_path=lake_path)
            for template in external.values():
                _run_external_command(
                    template,
                    project_root=project_root,
                    original_file=resolved,
                    lake_target=lake_target,
                    theorem_id=theorem_id,
                    timeout_s=timeout_s,
                )
            warm_probe = LeanProbe(
                auto_build=auto_build,
                local_repl_path=local_repl_path,
                lake_path=lake_path,
                verbose=verbose,
            )
            try:
                warm_probe.check_target(
                    resolved,
                    theorem_id=theorem_id,
                    cwd=project_root,
                    replacement=replacement,
                    timeout_s=timeout_s,
                )
            finally:
                warm_probe.close()
            if include_no_cache:
                _run_no_cache_probe_check(
                    file_path=resolved,
                    theorem_id=theorem_id,
                    project_root=project_root,
                    replacement=replacement,
                    timeout_s=timeout_s,
                    auto_build=auto_build,
                    local_repl_path=local_repl_path,
                    lake_path=lake_path,
                    verbose=verbose,
                )

        probe = LeanProbe(
            auto_build=auto_build,
            local_repl_path=local_repl_path,
            lake_path=lake_path,
            verbose=verbose,
        )
        prepare_start = time.perf_counter()
        prepare = probe.prepare_file(resolved, theorem_id=theorem_id, cwd=project_root, timeout_s=timeout_s)
        prepare_elapsed = time.perf_counter() - prepare_start
        if not prepare.get("success"):
            failures.append({"kind": "lean_probe_prepare", "output": str(prepare.get("error", ""))})
        elif not prepare.get("ok"):
            failures.append({"kind": "lean_probe_prepare", "output": str(prepare.get("output", ""))[:1000]})

        for _ in range(max(1, runs)):
            lake_ok, lake_elapsed, lake_output = _run_lake_check(
                project_root,
                lake_target,
                timeout_s,
                lake_path=lake_path,
            )
            lake_times.append(lake_elapsed)
            if not lake_ok:
                failures.append({"kind": "lake_env_lean", "output": lake_output})

            for name, template in external.items():
                external_ok, external_elapsed, external_output = _run_external_command(
                    template,
                    project_root=project_root,
                    original_file=resolved,
                    lake_target=lake_target,
                    theorem_id=theorem_id,
                    timeout_s=timeout_s,
                )
                external_times[name].append(external_elapsed)
                if not external_ok:
                    failures.append({"kind": f"external_command:{name}", "output": external_output[-1000:]})

            check = probe.check_target(
                resolved,
                theorem_id=theorem_id,
                cwd=project_root,
                replacement=replacement,
                timeout_s=timeout_s,
            )
            probe_times.append(float(check.get("elapsed_s", 0.0) or 0.0))
            if not check.get("success"):
                failures.append({"kind": "lean_probe_check", "output": str(check.get("error", ""))})
            elif not check.get("ok"):
                failures.append({"kind": "lean_probe_check", "output": str(check.get("output", ""))[:1000]})

            if include_feedback:
                feedback = probe.feedback(
                    resolved,
                    theorem_id=theorem_id,
                    cwd=project_root,
                    replacement=replacement,
                    timeout_s=timeout_s,
                )
                feedback_times.append(float(feedback.get("elapsed_s", 0.0) or 0.0))
                if not feedback.get("success"):
                    failures.append({"kind": "lean_probe_feedback", "output": str(feedback.get("error", ""))})
                elif not feedback.get("ok"):
                    failures.append({"kind": "lean_probe_feedback", "output": str(feedback.get("output", ""))[:1000]})

            if include_no_cache:
                no_cache, no_cache_elapsed = _run_no_cache_probe_check(
                    file_path=resolved,
                    theorem_id=theorem_id,
                    project_root=project_root,
                    replacement=replacement,
                    timeout_s=timeout_s,
                    auto_build=auto_build,
                    local_repl_path=local_repl_path,
                    lake_path=lake_path,
                    verbose=verbose,
                )
                no_cache_times.append(no_cache_elapsed)
                if not no_cache.get("success"):
                    failures.append({"kind": "lean_probe_no_cache_check", "output": str(no_cache.get("error", ""))})
                elif not no_cache.get("ok"):
                    failures.append({"kind": "lean_probe_no_cache_check", "output": str(no_cache.get("output", ""))[:1000]})
    finally:
        if probe is not None:
            probe.close()
        if cleanup_path is not None:
            try:
                cleanup_path.unlink()
            except FileNotFoundError:
                pass

    lake = _summary(lake_times)
    check = _summary(probe_times)
    feedback = _summary(feedback_times)
    no_cache = _summary(no_cache_times)
    lake_p50 = float(lake.get("p50", 0.0) or 0.0)
    check_p50 = float(check.get("p50", 0.0) or 0.0)
    feedback_p50 = float(feedback.get("p50", 0.0) or 0.0)
    no_cache_p50 = float(no_cache.get("p50", 0.0) or 0.0)
    prepare_s = round(prepare_elapsed, 3)
    speedup = round(lake_p50 / check_p50, 2) if check_p50 else 0.0
    result = {
        "success": not failures,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "project_root": str(project_root),
        "file": str(resolved),
        "lake_file": str(lake_target),
        "lake_target_warning": lake_target_warning,
        "replacement_warning": replacement_warning,
        "theorem_id": theorem_id,
        "runs": runs,
        "warmups": warmups,
        "platform": _platform_payload(),
        "benchmark_policy": {
            "lake_cache": "warm",
            "lean_probe_prepare": "fresh wall-clock prepare_file before timed checks",
            "replacement": "target declaration text",
            "warnings": "accepted when Lean exits 0",
        },
        "methodology": _methodology_payload(
            project_root=project_root,
            file_path=resolved,
            theorem_id=theorem_id,
            lake_target=lake_target,
        ),
        "lake_env_lean": lake,
        "lean_probe_check": check,
        "lean_probe_feedback": feedback if include_feedback else None,
        "lean_probe_no_cache_check": no_cache if include_no_cache else None,
        "external_commands": {name: _summary(times) for name, times in external_times.items()} or None,
        "lean_probe_prepare_s": prepare_s,
        "lake_env_lean_p50": lake_p50,
        "lean_probe_check_p50": check_p50,
        "lean_probe_feedback_p50": feedback_p50 if include_feedback else 0.0,
        "lean_probe_no_cache_check_p50": no_cache_p50 if include_no_cache else 0.0,
        "no_cache_penalty_vs_warm": round(no_cache_p50 / check_p50, 2) if include_no_cache and check_p50 else 0.0,
        "break_even_attempts": _break_even_attempts(
            prepare_s=prepare_s,
            lake_p50=lake_p50,
            check_p50=check_p50,
        ),
        "amortized_speedups": _amortized_speedups(
            prepare_s=prepare_s,
            lake_p50=lake_p50,
            check_p50=check_p50,
        ),
        "speedup_p50": speedup,
        "failures": failures[:5],
    }
    result_path = _write_result_json(result, results_dir, label or f"target-{theorem_id}")
    if result_path:
        result["result_path"] = result_path
    return result


def _load_benchmark_cases(cases_file: str | Path) -> list[BenchmarkCase]:
    path = Path(cases_file).expanduser().resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("cases", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("benchmark cases file must be a JSON list or an object with a `cases` list")

    cases: list[BenchmarkCase] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"benchmark case #{index + 1} must be an object")
        try:
            label = str(item["label"])
            file_path = Path(str(item["file_path"])).expanduser()
            theorem_id = str(item["theorem_id"])
        except KeyError as exc:
            raise ValueError(f"benchmark case #{index + 1} missing required key: {exc.args[0]}") from exc
        if not file_path.is_absolute():
            file_path = (path.parent / file_path).resolve()
        cases.append(
            BenchmarkCase(
                label=label,
                file_path=str(file_path),
                theorem_id=theorem_id,
                group=str(item.get("group", "") or ""),
                size=str(item.get("size", "") or ""),
                description=str(item.get("description", "") or ""),
            )
        )
    return cases


def run_benchmark_suite(
    *,
    cases_file: str | Path,
    cwd: str | Path | None = None,
    runs: int = 5,
    warmups: int = 1,
    include_feedback: bool = False,
    timeout_s: int = 120,
    auto_build: bool = False,
    local_repl_path: str | Path | None = None,
    lake_path: str | Path = "lake",
    verbose: bool = False,
    include_no_cache: bool = False,
    external_commands: Mapping[str, str] | None = None,
    results_dir: str | Path | None = None,
    case_labels: list[str] | None = None,
) -> dict[str, Any]:
    cases = _load_benchmark_cases(cases_file)
    wanted = set(case_labels or [])
    selected = [case for case in cases if not wanted or case.label in wanted]
    results: list[dict[str, Any]] = []
    for case in selected:
        result = run_benchmark(
            file_path=case.file_path,
            theorem_id=case.theorem_id,
            cwd=cwd,
            runs=runs,
            warmups=warmups,
            include_feedback=include_feedback,
            timeout_s=timeout_s,
            auto_build=auto_build,
            local_repl_path=local_repl_path,
            lake_path=lake_path,
            verbose=verbose,
            include_no_cache=include_no_cache,
            external_commands=external_commands,
            results_dir=results_dir,
            label=case.label,
        )
        result["case"] = {
            "label": case.label,
            "group": case.group,
            "size": case.size,
            "description": case.description,
            "file_path": case.file_path,
            "theorem_id": case.theorem_id,
        }
        results.append(result)
    successful = [item for item in results if item.get("success") and not item.get("failures")]
    suite = {
        "success": all(item.get("success") for item in results),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cases_file": str(Path(cases_file).expanduser().resolve()),
        "project_root": str(find_lean_project_root(cwd) if cwd else ""),
        "runs": runs,
        "warmups": warmups,
        "platform": _platform_payload(),
        "case_count": len(results),
        "successful_case_count": len(successful),
        "cases": results,
    }
    result_path = _write_result_json(suite, results_dir, "target-suite")
    if result_path:
        suite["result_path"] = result_path
    return suite


def _run_li_cutoffs(
    *,
    project_root: Path,
    file_path: Path,
    header: str,
    segments: list[Any],
    mode: str,
    timeout_s: int,
    auto_build: bool,
    local_repl_path: str | Path | None,
    lake_path: str | Path,
    verbose: bool,
) -> tuple[bool, float, list[dict[str, Any]], str]:
    probe = LeanProbe(
        auto_build=auto_build,
        local_repl_path=local_repl_path,
        lake_path=lake_path,
        verbose=verbose,
    )
    per_cutoff: list[dict[str, Any]] = []
    total_elapsed = 0.0
    try:
        session, session_error = probe._get_session(project_root, file_path)
        if session is None:
            return False, 0.0, [], session_error
        response, header_elapsed, header_error = probe._run_command(
            session.server,
            header,
            env=None,
            include_tactics=False,
            timeout_s=timeout_s,
            retry=lambda: probe._restart_incremental_server(session),
        )
        total_elapsed += header_elapsed
        if header_error:
            return False, total_elapsed, per_cutoff, header_error
        if not _response_ok(response):
            return False, total_elapsed, per_cutoff, "LeanInteract header warmup failed"
        header_env = getattr(response, "env", None)
        env = header_env
        for index, segment in enumerate(segments):
            cmd = "".join(item.text for item in segments[: index + 1]) if mode == "cumulative" else segment.text
            run_env = header_env if mode == "cumulative" else env
            response, elapsed, error = probe._run_command(
                session.server,
                cmd,
                env=run_env,
                include_tactics=False,
                timeout_s=timeout_s,
                retry=lambda: probe._restart_incremental_server(session),
            )
            total_elapsed += elapsed
            ok = not error and _response_ok(response)
            env = getattr(response, "env", None) if response is not None and mode == "delta" else env
            per_cutoff.append(
                {
                    "index": segment.index,
                    "name": segment.name,
                    "kind": segment.kind,
                    "end_line": segment.end_line,
                    "elapsed_s": round(elapsed, 3),
                    "ok": ok,
                    "error": error,
                }
            )
            if not ok:
                return False, total_elapsed, per_cutoff, error or f"LeanInteract {mode} cutoff failed"
    finally:
        probe.close()
    return True, total_elapsed, per_cutoff, ""


def _lake_cutoff_file(original: Path, header: str, segments: list[Any], cutoff_index: int) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(original.parent),
        prefix=".lean_probe_cutoff_",
        suffix=".lean",
        delete=False,
    )
    try:
        tmp.write(header.rstrip() + "\n")
        for segment in segments[: cutoff_index + 1]:
            tmp.write(segment.text.rstrip() + "\n")
    finally:
        tmp.close()
    return Path(tmp.name)


def _run_lake_cutoffs(
    *,
    project_root: Path,
    file_path: Path,
    header: str,
    segments: list[Any],
    timeout_s: int,
    lake_path: str | Path = "lake",
) -> tuple[bool, float, list[dict[str, Any]], str]:
    total_elapsed = 0.0
    per_cutoff: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        tmp_path = _lake_cutoff_file(file_path, header, segments, index)
        try:
            ok, elapsed, output = _run_lake_check(project_root, tmp_path, timeout_s, lake_path=lake_path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        total_elapsed += elapsed
        per_cutoff.append(
            {
                "index": segment.index,
                "name": segment.name,
                "kind": segment.kind,
                "end_line": segment.end_line,
                "elapsed_s": round(elapsed, 3),
                "ok": ok,
                "output": output[-1000:],
            }
        )
        if not ok:
            return False, total_elapsed, per_cutoff, output
    return True, total_elapsed, per_cutoff, ""


def run_queue_cutoff_benchmark(
    *,
    file_path: str | Path,
    cwd: str | Path | None = None,
    runs: int = 3,
    max_cutoffs: int = 0,
    timeout_s: int = 120,
    auto_build: bool = False,
    local_repl_path: str | Path | None = None,
    lake_path: str | Path = "lake",
    verbose: bool = False,
    results_dir: str | Path | None = None,
    label: str = "",
) -> dict[str, Any]:
    project_root = find_lean_project_root(cwd or file_path)
    if project_root is None:
        return {"success": False, "error": "Lean project root not detected"}
    resolved = _resolve_project_file(project_root, file_path)
    if not resolved.is_file():
        return {"success": False, "error": f"Lean file not found: {resolved}"}

    header, all_segments = segment_file(resolved.read_text(encoding="utf-8"))
    segments = all_segments[:max_cutoffs] if max_cutoffs and max_cutoffs > 0 else all_segments
    if not segments:
        return {"success": False, "error": "No Lean declaration cutoffs found"}

    lake_totals: list[float] = []
    cumulative_totals: list[float] = []
    delta_totals: list[float] = []
    failures: list[dict[str, str]] = []
    last_cutoffs: dict[str, list[dict[str, Any]]] = {}
    for _ in range(max(1, runs)):
        lake_ok, lake_elapsed, lake_cutoffs, lake_error = _run_lake_cutoffs(
            project_root=project_root,
            file_path=resolved,
            header=header,
            segments=segments,
            timeout_s=timeout_s,
            lake_path=lake_path,
        )
        lake_totals.append(lake_elapsed)
        last_cutoffs["lake_temp_cutoff"] = lake_cutoffs
        if not lake_ok:
            failures.append({"kind": "lake_temp_cutoff", "output": lake_error[-1000:]})

        cumulative_ok, cumulative_elapsed, cumulative_cutoffs, cumulative_error = _run_li_cutoffs(
            project_root=project_root,
            file_path=resolved,
            header=header,
            segments=segments,
            mode="cumulative",
            timeout_s=timeout_s,
            auto_build=auto_build,
            local_repl_path=local_repl_path,
            lake_path=lake_path,
            verbose=verbose,
        )
        cumulative_totals.append(cumulative_elapsed)
        last_cutoffs["leaninteract_header_plus_cumulative"] = cumulative_cutoffs
        if not cumulative_ok:
            failures.append({"kind": "leaninteract_header_plus_cumulative", "output": cumulative_error[-1000:]})

        delta_ok, delta_elapsed, delta_cutoffs, delta_error = _run_li_cutoffs(
            project_root=project_root,
            file_path=resolved,
            header=header,
            segments=segments,
            mode="delta",
            timeout_s=timeout_s,
            auto_build=auto_build,
            local_repl_path=local_repl_path,
            lake_path=lake_path,
            verbose=verbose,
        )
        delta_totals.append(delta_elapsed)
        last_cutoffs["leaninteract_header_plus_delta_seq"] = delta_cutoffs
        if not delta_ok:
            failures.append({"kind": "leaninteract_header_plus_delta_seq", "output": delta_error[-1000:]})

    lake = _summary(lake_totals)
    cumulative = _summary(cumulative_totals)
    delta = _summary(delta_totals)
    lake_p50 = float(lake.get("p50", 0.0) or 0.0)
    cumulative_p50 = float(cumulative.get("p50", 0.0) or 0.0)
    delta_p50 = float(delta.get("p50", 0.0) or 0.0)
    result = {
        "success": not failures,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "project_root": str(project_root),
        "file": str(resolved),
        "runs": runs,
        "cutoff_count": len(segments),
        "platform": _platform_payload(),
        "benchmark_policy": {
            "lake_temp_cutoff": "temp file containing header plus declarations up to cutoff",
            "leaninteract_header_plus_cumulative": "header env reused, full growing declaration prefix checked per cutoff",
            "leaninteract_header_plus_delta_seq": "header env reused, only next declaration chunk checked and env advanced",
            "warnings": "accepted when Lean exits 0 or LeanInteract has no hard errors",
        },
        "methodology": {
            "lean_file": str(resolved),
            "project_root": str(project_root),
            "surfaces": {
                "lake_temp_cutoff": "for each cutoff, write a temp `.lean` file with header plus declarations through that cutoff, then run `lake env lean`",
                "leaninteract_header_plus_cumulative": "start one LeanInteract server, check header once, then check growing declaration prefix at each cutoff against the header env",
                "leaninteract_header_plus_delta_seq": "start one LeanInteract server, check header once, then check only each next declaration chunk and advance env",
            },
            "acceptance_policy": {
                "lake": "process exit code 0; warnings accepted",
                "leaninteract": "response valid without sorry and no hard errors",
            },
        },
        "cutoffs": [
            {
                "index": segment.index,
                "kind": segment.kind,
                "name": segment.name,
                "start_line": segment.start_line,
                "end_line": segment.end_line,
            }
            for segment in segments
        ],
        "lake_temp_cutoff": lake,
        "leaninteract_header_plus_cumulative": cumulative,
        "leaninteract_header_plus_delta_seq": delta,
        "speedup_p50": {
            "cumulative_vs_lake": round(lake_p50 / cumulative_p50, 2) if cumulative_p50 else 0.0,
            "delta_vs_lake": round(lake_p50 / delta_p50, 2) if delta_p50 else 0.0,
        },
        "last_cutoff_details": last_cutoffs,
        "failures": failures[:5],
    }
    result_path = _write_result_json(result, results_dir, label or f"file-{resolved.stem}")
    if result_path:
        result["result_path"] = result_path
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run LeanProbe benchmarks.")
    sub = parser.add_subparsers(dest="command", required=True)

    target = sub.add_parser("target", help="Run one repeated target benchmark")
    target.add_argument("file_path")
    target.add_argument("theorem_id")
    target.add_argument("--cwd", default="")
    target.add_argument("--replacement", default="")
    target.add_argument("--replacement-file", default="")
    target.add_argument("--runs", type=int, default=5)
    target.add_argument("--warmups", type=int, default=1)
    target.add_argument("--include-feedback", action="store_true")
    target.add_argument("--include-no-cache", action="store_true")
    target.add_argument(
        "--external-command",
        action="append",
        default=[],
        help="Additional verifier timing as NAME=COMMAND; placeholders: {file}, {original}, {cwd}, {theorem}",
    )
    target.add_argument("--timeout-s", type=int, default=120)
    target.add_argument("--results-dir", default="")
    target.add_argument("--label", default="")
    target.add_argument("--pretty", action="store_true")

    suite = sub.add_parser("suite", help="Run a JSON benchmark case suite")
    suite.add_argument("--cases-file", required=True)
    suite.add_argument("--cwd", default="")
    suite.add_argument("--runs", type=int, default=5)
    suite.add_argument("--warmups", type=int, default=1)
    suite.add_argument("--include-feedback", action="store_true")
    suite.add_argument("--include-no-cache", action="store_true")
    suite.add_argument(
        "--external-command",
        action="append",
        default=[],
        help="Additional verifier timing as NAME=COMMAND; placeholders: {file}, {original}, {cwd}, {theorem}",
    )
    suite.add_argument("--case", action="append", default=[])
    suite.add_argument("--timeout-s", type=int, default=120)
    suite.add_argument("--results-dir", default="")
    suite.add_argument("--pretty", action="store_true")

    file_benchmark = sub.add_parser("file", help="Run same-file sequential cutoff benchmark")
    file_benchmark.add_argument("file_path")
    file_benchmark.add_argument("--cwd", default="")
    file_benchmark.add_argument("--runs", type=int, default=3)
    file_benchmark.add_argument("--max-cutoffs", type=int, default=0)
    file_benchmark.add_argument("--timeout-s", type=int, default=120)
    file_benchmark.add_argument("--results-dir", default="")
    file_benchmark.add_argument("--label", default="")
    file_benchmark.add_argument("--pretty", action="store_true")

    args = parser.parse_args()
    external_commands = _external_command_specs(getattr(args, "external_command", []))
    if args.command == "target":
        replacement = args.replacement
        if args.replacement_file:
            replacement = Path(args.replacement_file).read_text(encoding="utf-8")
        result = run_benchmark(
            file_path=args.file_path,
            theorem_id=args.theorem_id,
            cwd=args.cwd or None,
            replacement=replacement,
            runs=args.runs,
            warmups=args.warmups,
            include_feedback=args.include_feedback,
            include_no_cache=args.include_no_cache,
            external_commands=external_commands,
            timeout_s=args.timeout_s,
            results_dir=args.results_dir or None,
            label=args.label,
        )
    elif args.command == "suite":
        result = run_benchmark_suite(
            cases_file=args.cases_file,
            cwd=args.cwd or None,
            runs=args.runs,
            warmups=args.warmups,
            include_feedback=args.include_feedback,
            include_no_cache=args.include_no_cache,
            external_commands=external_commands,
            timeout_s=args.timeout_s,
            results_dir=args.results_dir or None,
            case_labels=args.case or None,
        )
    else:
        result = run_queue_cutoff_benchmark(
            file_path=args.file_path,
            cwd=args.cwd or None,
            runs=args.runs,
            max_cutoffs=args.max_cutoffs,
            timeout_s=args.timeout_s,
            results_dir=args.results_dir or None,
            label=args.label,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    main()
