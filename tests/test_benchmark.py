from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lean_probe import benchmark


def test_amortized_speedups_and_break_even_attempts():
    assert benchmark._break_even_attempts(prepare_s=3.0, lake_p50=4.0, check_p50=1.0) == 1
    assert benchmark._break_even_attempts(prepare_s=9.0, lake_p50=4.0, check_p50=1.0) == 3
    assert benchmark._break_even_attempts(prepare_s=1.0, lake_p50=1.0, check_p50=2.0) is None

    speedups = benchmark._amortized_speedups(prepare_s=3.0, lake_p50=4.0, check_p50=1.0)

    assert speedups == {"1": 1.0, "3": 2.0, "10": 3.08}


def test_target_replacement_extracts_named_declaration(tmp_path):
    target = tmp_path / "Demo.lean"
    target.write_text(
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem first : True := by",
                "  trivial",
                "",
                "theorem second : True := by",
                "  trivial",
                "",
            ]
        ),
        encoding="utf-8",
    )

    replacement, warning = benchmark._target_replacement(target, "second")

    assert warning == ""
    assert replacement.startswith("theorem second")
    assert "trivial" in replacement


def test_segmentation_attaches_standalone_attribute_to_following_declaration():
    text = "\n".join(
        [
            "import Mathlib",
            "",
            "def first : Nat := 1",
            "@[simp]",
            "theorem second : first = 1 := by",
            "  rfl",
            "",
        ]
    )

    _header, segments = benchmark.segment_file(text)

    assert segments[0].text.strip() == "def first : Nat := 1"
    assert segments[1].text.startswith("@[simp]\ntheorem second")


def test_partial_declaration_replaces_body_with_sorry():
    text = "theorem demo (n : Nat) : n = n := by\n  rfl\n"

    partial = benchmark._partial_declaration(text)

    assert partial == "theorem demo (n : Nat) : n = n := by\n  sorry\n"


def test_full_scenario_file_replaces_only_current_declaration(tmp_path):
    lean_file = tmp_path / "Demo.lean"
    lean_file.write_text(
        "import Mathlib\n\n"
        "theorem first : True := by\n"
        "  trivial\n\n"
        "theorem second : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    header, segments = benchmark.segment_file(lean_file.read_text(encoding="utf-8"))
    scenario = {
        "index": segments[0].index,
        "text": "theorem first : True := by\n  sorry\n",
    }

    tmp_path_full = benchmark._lake_full_scenario_file(lean_file, header, segments, scenario)

    try:
        text = tmp_path_full.read_text(encoding="utf-8")
    finally:
        tmp_path_full.unlink()
    assert "theorem first : True := by\n  sorry\n" in text
    assert "theorem second : True := by\n  trivial\n" in text


def test_scenario_status_accepts_expected_partial_sorry():
    partial_status = {"process_ok": True, "has_errors": False, "has_sorry": True, "valid_without_sorry": False}
    full_status = {"process_ok": True, "has_errors": False, "has_sorry": False, "valid_without_sorry": True}

    assert benchmark._scenario_accepted(partial_status, variant="partial") is True
    assert benchmark._scenario_accepted(full_status, variant="full") is True
    assert benchmark._scenario_accepted({**partial_status, "has_sorry": False}, variant="partial") is False


def test_lake_status_accepts_structured_adapter_json():
    status = benchmark._lake_status(True, '{"success": true, "ok": false, "has_errors": false, "has_sorry": true}')

    assert status == {
        "process_ok": True,
        "has_errors": False,
        "has_sorry": True,
        "valid_without_sorry": False,
    }


def test_benchmark_writes_result_json(tmp_path):
    result = {"success": True, "value": 1}

    path = benchmark._write_result_json(result, tmp_path / "results", "demo")

    assert path.endswith("demo.json")
    assert json.loads((tmp_path / "results" / "demo.json").read_text(encoding="utf-8")) == result


def test_response_ok_accepts_warnings_without_errors():
    response = SimpleNamespace(
        has_errors=lambda: False,
        lean_code_is_valid=lambda allow_sorry=False: True,
    )

    assert benchmark._response_ok(response) is True


def test_hard_error_detection_ignores_warnings():
    assert benchmark._has_hard_lean_error("Demo.lean:1:1: warning: style issue") is False
    assert benchmark._has_hard_lean_error("Demo.lean:1:1: error: unknown identifier") is True


def test_last_json_object_skips_logs_after_json():
    payload = benchmark._last_json_object("startup log\n{\"ok\": true}\ncleanup log")

    assert payload == {"ok": True}


def test_external_command_specs_parse_name_and_command():
    specs = benchmark._external_command_specs(["lake-direct=lake env lean {file}"])

    assert specs == {"lake-direct": "lake env lean {file}"}


def test_run_text_command_reports_timeout(tmp_path):
    ok, elapsed, output = benchmark._run_text_command(
        ["python", "-c", "import time; time.sleep(2)"],
        cwd=tmp_path,
        timeout_s=1,
    )

    assert ok is False
    assert elapsed >= 1
    assert "timed out after 1s" in output


def test_external_command_allows_json_braces(tmp_path):
    original = tmp_path / "Original.lean"
    target = tmp_path / "Candidate.lean"
    original.write_text("theorem original : True := by\n  trivial\n", encoding="utf-8")
    target.write_text("theorem candidate : True := by\n  trivial\n", encoding="utf-8")

    ok, _elapsed, output = benchmark._run_external_command(
        "python -c 'import json; print(json.dumps({\"success\": True, \"file\": \"{file}\"}))'",
        project_root=tmp_path,
        original_file=original,
        lake_target=target,
        theorem_id="candidate",
        timeout_s=5,
    )

    assert ok is True
    payload = benchmark._last_json_object(output)
    assert payload is not None
    assert payload["success"] is True
    assert payload["file"] == str(target)


def test_methodology_payload_names_surfaces(tmp_path):
    project = tmp_path / "Project"
    project.mkdir()
    lean_file = project / "Main.lean"
    lake_file = project / ".lean_probe_bench_demo.lean"

    payload = benchmark._methodology_payload(
        project_root=project,
        file_path=lean_file,
        theorem_id="demo",
        lake_target=lake_file,
    )

    assert payload["lean_file"] == str(lean_file)
    assert payload["target_declaration"] == "demo"
    assert payload["lake_temp_file"] == str(lake_file)
    assert "terminal_lake_env_lean" in payload["surfaces"]
    assert "lean_probe_no_cache_check" in payload["surfaces"]
    external_name = "epf" + "lemma"
    assert not any(external_name in key.lower() for key in payload["surfaces"])


def test_load_benchmark_cases_resolves_relative_paths(tmp_path):
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    lean_file = lean_dir / "Demo.lean"
    lean_file.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "label": "demo_case",
                        "file_path": "lean/Demo.lean",
                        "theorem_id": "demo",
                        "group": "unit",
                        "size": "short",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = benchmark._load_benchmark_cases(cases_file)

    assert len(cases) == 1
    assert cases[0].label == "demo_case"
    assert cases[0].file_path == str(lean_file.resolve())


def test_example_benchmark_cases_point_to_existing_targets():
    repo_root = Path(__file__).resolve().parents[1]
    cases = benchmark._load_benchmark_cases(repo_root / "examples" / "benchmark_cases.json")

    assert len(cases) >= 12
    for case in cases:
        lean_file = Path(case.file_path)
        assert lean_file.is_file(), case.file_path
        _, segments = benchmark.segment_file(lean_file.read_text(encoding="utf-8"))
        names = {segment.name for segment in segments}
        assert case.theorem_id in names, case.label


def test_icml26_examples_are_longer_complete_sources():
    repo_root = Path(__file__).resolve().parents[1]
    expected_min_lines = {
        "icml26_binary_heap.lean": 150,
        "icml26_treap_analysis.lean": 60,
        "icml26_weighted_graph_prefix.lean": 180,
    }

    for filename, min_lines in expected_min_lines.items():
        lean_file = repo_root / "examples" / "lean" / filename
        text = lean_file.read_text(encoding="utf-8")
        _, segments = benchmark.segment_file(text)

        assert text.count("\n") + 1 >= min_lines
        assert "sorry" not in text
        assert "admit" not in text
        assert segments
