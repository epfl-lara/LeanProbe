from __future__ import annotations

from types import SimpleNamespace

import pytest

from lean_probe import core
from lean_probe.core import LeanProbe, segment_file


def _write_project(tmp_path, text: str):
    project = tmp_path / "Demo"
    module_dir = project / "Demo"
    module_dir.mkdir(parents=True)
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    target = module_dir / "Main.lean"
    target.write_text(text, encoding="utf-8")
    return project, target


def _install_fake_lean_interact(monkeypatch):
    servers = []

    class _Command:
        def __init__(self, *, cmd: str, all_tactics: bool = False, env=None):
            self.cmd = cmd
            self.all_tactics = all_tactics
            self.env = env

        def model_copy(self, *, update):
            copied = _Command(cmd=self.cmd, all_tactics=self.all_tactics, env=self.env)
            for key, value in update.items():
                setattr(copied, key, value)
            return copied

    class _ProofStep:
        def __init__(self, *, proof_state: int, tactic: str):
            self.proof_state = proof_state
            self.tactic = tactic

    class _Response:
        def __init__(self, *, env: int, errors: bool = False, tactics=None, sorries=None):
            self.env = env
            self.messages = []
            self.sorries = sorries or []
            self.tactics = tactics or []
            self._errors = errors
            if errors:
                pos = SimpleNamespace(line=1, column=7)
                self.messages.append(
                    SimpleNamespace(
                        severity="error",
                        data="unexpected token",
                        start_pos=pos,
                        end_pos=pos,
                    )
                )

        def has_errors(self):
            return self._errors

        def lean_code_is_valid(self, *, allow_sorry: bool = False):
            return not self._errors and (allow_sorry or not self.sorries)

    class _StepResponse:
        proof_state = 77
        goals = []
        proof_status = "Completed"

    class _Server:
        def __init__(self, config):
            self.config = config
            self.runs = []
            self.killed = False
            servers.append(self)

        def run(self, request, timeout=None):
            if isinstance(request, _ProofStep):
                self.runs.append({"proof_state": request.proof_state, "tactic": request.tactic, "timeout": timeout})
                return _StepResponse()
            self.runs.append(
                {
                    "cmd": request.cmd,
                    "env": request.env,
                    "all_tactics": request.all_tactics,
                    "timeout": timeout,
                }
            )
            if "by sorry" in request.cmd or request.cmd.strip().endswith("sorry"):
                pos = SimpleNamespace(line=1, column=40)
                sorry = SimpleNamespace(start_pos=pos, end_pos=pos, goal="n : Nat\n⊢ n = n", proof_state=5)
                return _Response(env=100 + len(self.runs), sorries=[sorry])
            if "bad" in request.cmd:
                return _Response(env=100 + len(self.runs), errors=True)
            tactics = []
            if request.all_tactics:
                pos = SimpleNamespace(line=1, column=0)
                tactics.append(
                    SimpleNamespace(
                        tactic="trivial",
                        goals="⊢ True",
                        proof_state="⊢ True",
                        start_pos=pos,
                        end_pos=pos,
                        used_constants=["True.intro"],
                    )
                )
            return _Response(env=100 + len(self.runs), tactics=tactics)

        def kill(self):
            self.killed = True

    class _Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Project:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(core, "_import_lean_interact", lambda: (_Command, _ProofStep, _Config, _Server, _Project, ""))
    monkeypatch.setattr(core, "_local_repl_dir", lambda project_root: project_root / ".lake" / "packages" / "repl")
    return servers


def test_segment_file_keeps_doc_comment_with_declaration():
    header, segments = segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "/-- First theorem. -/",
                "theorem first : True := by",
                "  trivial",
                "",
                "lemma second : True := by",
                "  trivial",
                "",
            ]
        )
    )

    assert header == "import Mathlib\n"
    assert [segment.name for segment in segments] == ["first", "second"]
    assert segments[0].text.startswith("/-- First theorem. -/")
    assert segments[0].start_line == 3
    assert segments[1].start_line == 7


def test_segment_file_ignores_keywords_inside_comments_and_strings():
    header, segments = segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "/-- theorem fake : True := by trivial -/",
                "theorem real : True := by",
                '  have s := "def also_fake := 1"',
                "  trivial",
                "",
                "/-",
                "lemma hidden : True := by trivial",
                "-/",
                "def actual : Nat := 1",
                "",
            ]
        )
    )

    assert header == "import Mathlib\n"
    assert [segment.name for segment in segments] == ["real", "actual"]


def test_segment_file_recognizes_instance_declarations():
    _header, segments = segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "instance demoInst : Inhabited Nat := ⟨0⟩",
                "",
            ]
        )
    )

    assert len(segments) == 1
    assert segments[0].kind == "instance"
    assert segments[0].name == "demoInst"


def test_segment_file_recognizes_modifiers_compound_attributes_and_more_kinds():
    text = "\n".join(
        [
            "import Mathlib",
            "",
            "@[simp, reducible]",
            "private theorem private_thm : True := by",
            "  trivial",
            "",
            "lemma αβγ : True := by",
            "  trivial",
            "",
            "noncomputable abbrev hiddenValue : Nat := 1",
            "",
            "protected structure Box where",
            "  value : Nat",
            "",
            "inductive Color where",
            "  | red",
            "",
            "class HasFoo (α : Type) where",
            "  foo : α",
            "",
            "axiom trusted : True",
            "",
            "opaque secret : Nat",
            "",
        ]
    )

    _header, segments = segment_file(text)

    assert [(segment.kind, segment.name) for segment in segments] == [
        ("theorem", "private_thm"),
        ("lemma", "αβγ"),
        ("abbrev", "hiddenValue"),
        ("structure", "Box"),
        ("inductive", "Color"),
        ("class", "HasFoo"),
        ("axiom", "trusted"),
        ("opaque", "secret"),
    ]
    assert segments[0].text.startswith("@[simp, reducible]\nprivate theorem")


def test_segment_file_strips_universe_params_from_names():
    _header, segments = segment_file(
        "\n".join(
            [
                "theorem foo.{u} (α : Sort u) : True := by",
                "  trivial",
                "",
                "instance inst.{u, v} : Inhabited (Sort u) := ⟨PUnit⟩",
                "",
            ]
        )
    )

    assert [(segment.kind, segment.name) for segment in segments] == [
        ("theorem", "foo"),
        ("instance", "inst"),
    ]
    assert core._find_segment(segments, "foo").name == "foo"
    assert core._find_segment(segments, "inst").name == "inst"


def test_segment_file_does_not_capture_invalid_identifier_characters():
    _header, segments = segment_file("theorem foo*bar : True := by\n  trivial\n")

    assert segments[0].kind == "theorem"
    assert segments[0].name == ""
    assert core._find_segment(segments, "foo*bar") is None


def test_segment_file_keeps_mutual_block_as_one_context_chunk():
    text = "\n".join(
        [
            "import Mathlib",
            "",
            "theorem before : True := by",
            "  trivial",
            "",
            "mutual",
            "  def evenly : Nat → Bool",
            "    | 0 => true",
            "    | n + 1 => oddly n",
            "",
            "  def oddly : Nat → Bool",
            "    | 0 => false",
            "    | n + 1 => evenly n",
            "end",
            "",
            "theorem after : True := by",
            "  trivial",
            "",
        ]
    )

    _header, segments = segment_file(text)

    assert [(segment.kind, segment.name) for segment in segments] == [
        ("theorem", "before"),
        ("mutual", ""),
        ("theorem", "after"),
    ]
    assert "mutual" not in segments[0].text
    assert "def evenly" in segments[1].text
    assert "def oddly" in segments[1].text


def test_find_segment_matches_qualified_and_short_names():
    _header, segments = segment_file("namespace N\n\ntheorem demo : True := by\n  trivial\n\nend N\n")

    assert core._find_segment(segments, "demo").name == "demo"
    assert core._find_segment(segments, "N.demo").name == "demo"
    assert core._find_segment(segments, "missing") is None


def test_feedback_lean_preserves_indentation_and_truncates():
    text = "theorem demo : True := by\n  exact False.elim ?h\n"
    messages = [
        {
            "severity": "error",
            "message": "x" * 400,
            "start": {"line": 2, "column": 2},
        }
    ]
    tactics = [{"goals": "⊢ True", "start": {"line": 2, "column": 2}}]

    feedback = core._feedback_lean(text, messages, tactics)

    assert "  /- <feedback>" in feedback
    assert "-- proof state: ⊢ True" in feedback
    assert "x" * 260 not in feedback


def test_dead_server_error_tokens_are_stable():
    for text in [
        "Lean server is not running",
        "broken pipe",
        "connection reset by peer",
        "process has exited",
    ]:
        assert core._dead_server_error(text) is True


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("request timed out", "timeout"),
        ("failed to start LeanInteract server: no such file", "lean_interact_start_failed"),
        ("lean-interact unavailable: missing", "lean_interact_unavailable"),
        ("Lean server is not running", "dead_server"),
        ("Lean project root not detected", "no_project_root"),
        ("Lean file not found", "file_not_found"),
        ("target declaration not found", "target_not_found"),
        ("LeanInteract header warmup failed", "header_failed"),
        ("failed to build env before target at demo", "prior_decl_failed"),
        ("unexpected backend failure", "backend_error"),
    ],
)
def test_error_code_for_message_is_stable(message, code):
    assert core._error_code_for_message(message) == code


def test_error_code_startup_failure_takes_precedence_over_dead_server_text():
    assert (
        core._error_code_for_message("failed to start LeanInteract server: broken pipe") == "lean_interact_start_failed"
    )


def test_capabilities_reports_degraded_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        core, "_import_lean_interact", lambda: (None, None, None, None, None, "lean-interact unavailable: missing")
    )
    monkeypatch.setattr(LeanProbe, "_resolve_project_root", lambda self, cwd, file_path=None: None)
    probe = LeanProbe()

    payload = probe.capabilities(tmp_path)

    assert payload["available"] is False
    assert "lean_interact_unavailable" in payload["degraded_codes"]
    assert "no_project_root" in payload["degraded_codes"]


def test_check_target_reuses_header_and_prior_declaration_env(monkeypatch, tmp_path):
    servers = _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(
        tmp_path,
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
    )
    probe = LeanProbe()

    first = probe.check_target(target, theorem_id="second", cwd=project)
    second = probe.check_target(target, theorem_id="second", cwd=project)

    assert first["ok"] is True
    assert first["cache"]["cache_hit"] is False
    assert second["ok"] is True
    assert second["cache"]["cache_hit"] is True
    assert [run["cmd"].strip().splitlines()[0] for run in servers[0].runs] == [
        "import Mathlib",
        "theorem first : True := by",
        "theorem second : True := by",
        "theorem second : True := by",
    ]
    assert servers[0].runs[2]["env"] == 102
    assert servers[0].runs[3]["env"] == 102


def test_check_target_reports_chunk_and_file_locations_on_failure(monkeypatch, tmp_path):
    servers = _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(
        tmp_path,
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem demo : True := by",
                "  trivial",
                "",
            ]
        ),
    )
    probe = LeanProbe()

    payload = probe.check_target(
        target,
        theorem_id="demo",
        cwd=project,
        replacement="theorem demo : True := by\n  bad\n",
    )

    assert payload["success"] is True
    assert payload["ok"] is False
    assert payload["has_errors"] is True
    assert payload["messages"][0]["start"] == {"line": 1, "column": 7}
    assert payload["messages"][0]["file_start"] == {"line": 3, "column": 7}
    assert "/- <feedback>" in payload["feedback_lean"]
    assert servers[0].runs[-1]["all_tactics"] is True


def test_check_target_success_does_not_rerun_with_tactics(monkeypatch, tmp_path):
    servers = _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(
        tmp_path,
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem demo : True := by",
                "  trivial",
                "",
            ]
        ),
    )
    probe = LeanProbe()

    payload = probe.check_target(target, theorem_id="demo", cwd=project)

    assert payload["ok"] is True
    assert [run["all_tactics"] for run in servers[0].runs] == [False, False]


def test_target_not_found_returns_error_code(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe()

    payload = probe.check_target(target, theorem_id="missing", cwd=project)

    assert payload["success"] is False
    assert payload["error_code"] == "target_not_found"


def test_explicit_invalid_cwd_does_not_fall_back_to_file_project(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    invalid = tmp_path / "NotAProject"
    invalid.mkdir()
    probe = LeanProbe()

    payload = probe.check_target(target, theorem_id="demo", cwd=invalid)

    assert payload["success"] is False
    assert payload["error_code"] == "no_project_root"


def test_proof_state_explicit_invalid_cwd_returns_project_error(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    invalid = tmp_path / "NotAProject"
    invalid.mkdir()
    probe = LeanProbe()

    payload = probe.proof_state_from_code("theorem ex : True := by sorry", cwd=invalid)

    assert payload["success"] is False
    assert payload["error_code"] == "no_project_root"


def test_prepare_target_not_found_returns_error_code(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe()

    payload = probe.prepare_file(target, theorem_id="missing", cwd=project)

    assert payload["success"] is False
    assert payload["error_code"] == "target_not_found"


def test_header_change_restarts_incremental_session(monkeypatch, tmp_path):
    servers = _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(
        tmp_path,
        "import Mathlib\n\ntheorem demo : True := by\n  trivial\n",
    )
    probe = LeanProbe()

    assert probe.check_target(target, theorem_id="demo", cwd=project)["ok"] is True
    target.write_text("import Std\n\ntheorem demo : True := by\n  trivial\n", encoding="utf-8")
    assert probe.check_target(target, theorem_id="demo", cwd=project)["ok"] is True

    assert len(servers) == 2


def test_resolve_file_path_prefers_project_root(tmp_path):
    project = tmp_path / "Project"
    project.mkdir()
    probe = LeanProbe()

    assert probe._resolve_file_path("Demo/Main.lean", project) == (project / "Demo" / "Main.lean").resolve()


def test_timeout_error_sets_structured_fields(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(
        tmp_path,
        "import Mathlib\n\ntheorem timeout_demo : True := by\n  trivial\n",
    )
    original_new_session = LeanProbe._new_session

    class _TimeoutOnTarget:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def run(self, request, timeout=None):
            if "timeout_demo" in getattr(request, "cmd", ""):
                raise TimeoutError("timed out")
            return self._wrapped.run(request, timeout=timeout)

    def _new_session(self, project_root, file_path, repl_dir):
        session, error = original_new_session(self, project_root, file_path, repl_dir)
        if session is not None:
            session.server = _TimeoutOnTarget(session.server)
        return session, error

    monkeypatch.setattr(LeanProbe, "_new_session", _new_session)
    probe = LeanProbe()

    payload = probe.check_target(target, theorem_id="timeout_demo", cwd=project)

    assert payload["success"] is False
    assert payload["timed_out"] is True
    assert payload["error_code"] == "timeout"


def test_proof_state_and_tactic_step(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, _target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe()

    state = probe.proof_state_from_code("theorem ex (n : Nat) : n = n := by sorry", cwd=project)
    step = probe.tactic_step(state["session_id"], state["sorries"][0]["proof_state"], "rfl")

    assert state["success"] is True
    assert state["sorries"][0]["proof_state"] == 5
    assert step["ok"] is True
    assert step["proof_status"] == "Completed"


def test_close_state_releases_session(monkeypatch, tmp_path):
    servers = _install_fake_lean_interact(monkeypatch)
    project, _target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe()

    state = probe.proof_state_from_code("theorem ex (n : Nat) : n = n := by sorry", cwd=project)
    state_server = servers[-1]
    closed = probe.close_state(state["session_id"])
    second_close = probe.close_state(state["session_id"])

    assert closed["ok"] is True
    assert state_server.killed is True
    assert second_close["success"] is False
    assert second_close["error_code"] == "unknown_session"


def test_code_sessions_are_lru_bounded(monkeypatch, tmp_path):
    servers = _install_fake_lean_interact(monkeypatch)
    project, _target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe(max_code_sessions=2)

    first = probe.proof_state_from_code("theorem a : True := by sorry", cwd=project)
    second = probe.proof_state_from_code("theorem b : True := by sorry", cwd=project)
    third = probe.proof_state_from_code("theorem c : True := by sorry", cwd=project)

    assert list(probe._code_sessions.keys()) == [second["session_id"], third["session_id"]]
    assert first["session_id"] not in probe._code_sessions
    assert servers[0].killed is True


def test_tactic_step_dead_server_marks_session_dead(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, _target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe()
    state = probe.proof_state_from_code("theorem ex (n : Nat) : n = n := by sorry", cwd=project)
    session = probe._code_sessions[state["session_id"]]

    class _DeadStepServer:
        def run(self, request, timeout=None):
            raise RuntimeError("Lean server is not running")

        def kill(self):
            pass

    session.server = _DeadStepServer()

    step = probe.tactic_step(state["session_id"], state["sorries"][0]["proof_state"], "rfl")

    assert step["success"] is False
    assert step["error_code"] == "session_dead"
    assert step["session_dead"] is True
    assert step["hint"] == "call lean_probe_state again"
    assert state["session_id"] not in probe._code_sessions


def test_proof_state_without_sorry_is_not_ok(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, _target = _write_project(tmp_path, "theorem demo : True := by\n  trivial\n")
    probe = LeanProbe()

    state = probe.proof_state_from_code("theorem ex : True := by trivial", cwd=project)

    assert state["success"] is True
    assert state["ok"] is False
    assert state["sorries"] == []


def test_check_target_restarts_dead_lean_server_once(monkeypatch, tmp_path):
    _install_fake_lean_interact(monkeypatch)
    project, target = _write_project(
        tmp_path,
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem demo : True := by",
                "  trivial",
                "",
            ]
        ),
    )
    failures = {"remaining": 1}
    original_new_session = LeanProbe._new_session

    class _DeadOnce:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def run(self, request, timeout=None):
            if failures["remaining"]:
                failures["remaining"] -= 1
                raise RuntimeError("The Lean server is not running.")
            return self._wrapped.run(request, timeout=timeout)

    def _new_session(self, project_root, file_path, repl_dir):
        session, error = original_new_session(self, project_root, file_path, repl_dir)
        if session is not None:
            session.server = _DeadOnce(session.server)
        return session, error

    monkeypatch.setattr(LeanProbe, "_new_session", _new_session)
    probe = LeanProbe()

    payload = probe.check_target(target, theorem_id="demo", cwd=project)

    assert payload["success"] is True
    assert payload["ok"] is True
    assert failures["remaining"] == 0
