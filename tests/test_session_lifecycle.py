"""Exercise worker bounds and caller-owned project generations with a fake backend."""

from pathlib import Path

import pytest

from lean_probe import LeanProbe, sessions

SOURCE = "import Mathlib\n\ntheorem first : True := by trivial\ntheorem second : True := by trivial\n"


def _project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Write a separate Lake project so project-scoped lifecycle checks are meaningful."""

    project = tmp_path / name
    project.mkdir()
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    target = project / "Main.lean"
    target.write_text(SOURCE, encoding="utf-8")
    return project, target


def test_file_workers_are_lru_bounded_and_eviction_rebuilds_prefix(fake_backend, lean_project):
    state = fake_backend()
    project, first = lean_project(SOURCE)
    second = first.with_name("Second.lean")
    third = first.with_name("Third.lean")
    second.write_text(SOURCE, encoding="utf-8")
    third.write_text(SOURCE, encoding="utf-8")
    probe = LeanProbe(max_file_sessions=2)

    for target in (first, second, first, third):
        assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]

    assert len(probe._sessions) == 2
    assert state["servers"][0].killed is False  # first was touched again
    assert state["servers"][1].killed is True
    checked = probe.check_target(second, theorem_id="second", cwd=project)
    assert checked["ok"] and checked["cache"]["cache_hit"] is False
    assert [run["cmd"].strip() for run in state["servers"][-1].runs] == [
        "import Mathlib",
        "theorem first : True := by trivial",
        "theorem second : True := by trivial",
    ]
    assert all(run["env"] is None or run["env"] in state["servers"][-1].owned for run in state["servers"][-1].runs)


def test_scratch_workers_are_lru_bounded_and_recreated(fake_backend, tmp_path):
    state = fake_backend()
    projects = [_project(tmp_path, name)[0] for name in ("A", "B", "C")]
    probe = LeanProbe(max_scratch_sessions=2)

    for project in (projects[0], projects[1], projects[0], projects[2]):
        assert probe.check_code("theorem t : True := by trivial", cwd=project)["ok"]

    assert list(probe._scratch_sessions) == [str(projects[0]), str(projects[2])]
    assert state["servers"][0].killed is False
    assert state["servers"][1].killed is True
    restarted = probe.check_code("theorem t : True := by trivial", cwd=projects[1])
    assert restarted["ok"] and restarted["cache"]["reused_server"] is False


def test_status_reports_every_pool_limit_and_scratch_project(fake_backend, lean_project):
    fake_backend()
    project, _ = lean_project(SOURCE)
    probe = LeanProbe(max_file_sessions=3, max_scratch_sessions=2, max_code_sessions=5)
    status = probe.capabilities(project, warm=True)
    assert status["max_file_sessions"] == 3
    assert status["max_scratch_sessions"] == 2
    assert status["max_code_sessions"] == 5
    assert status["scratch_sessions"] == [str(project)]


def test_close_project_preserves_other_projects_and_is_idempotent(fake_backend, tmp_path):
    state = fake_backend()
    first, first_target = _project(tmp_path, "A")
    other, other_target = _project(tmp_path, "B")
    probe = LeanProbe()
    opened = {}
    for project, target in ((first, first_target), (other, other_target)):
        assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
        assert probe.check_code("theorem t : True := by trivial", cwd=project)["ok"]
        opened[project] = probe.proof_state_from_code("theorem t : True := by sorry", cwd=project)

    closed = probe.close_project(first)
    assert closed["ok"] and closed["closed_sessions"] == {"file": 1, "scratch": 1, "proof_state": 1}
    assert [server.killed for server in state["servers"]] == [True, True, True, False, False, False]
    assert probe.tactic_step(opened[first]["session_id"], 5, "trivial")["error_code"] == "unknown_session"
    assert probe.tactic_step(opened[other]["session_id"], 5, "trivial")["ok"]
    assert probe.close_project(first)["closed_sessions"] == {"file": 0, "scratch": 0, "proof_state": 0}
    assert probe.check_target(other_target, theorem_id="second", cwd=other)["cache"]["cache_hit"]


def test_close_project_rejects_unresolved_root_without_closing_other_workers(fake_backend, tmp_path):
    state = fake_backend()
    project, target = _project(tmp_path, "A")
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    result = probe.close_project(tmp_path / "Missing")
    assert result["error_code"] == "no_project_root"
    assert state["servers"][0].killed is False


def test_failed_status_does_not_invalidate_standalone_workers(fake_backend, monkeypatch, tmp_path):
    state = fake_backend()
    monkeypatch.chdir(tmp_path)
    probe = LeanProbe()
    assert probe.check_code("theorem t : True := by trivial", generation="standalone-v1")["ok"]
    result = probe.capabilities(tmp_path / "Missing", generation="unrelated-v2")
    assert result["available"] is False
    assert state["servers"][0].killed is False
    assert probe._project_generations[""] == "standalone-v1"


def test_same_generation_preserves_warm_state_and_changed_generation_discards_all_families(fake_backend, tmp_path):
    state = fake_backend()
    project, target = _project(tmp_path, "A")
    other, other_target = _project(tmp_path, "B")
    probe = LeanProbe()
    first = probe.check_target(target, theorem_id="second", cwd=project, generation="built-v1")
    warm = probe.check_target(target, theorem_id="second", cwd=project, generation="built-v1")
    assert first["cache"]["generation"] == warm["cache"]["generation"] == "built-v1"
    assert warm["cache"]["cache_hit"] is True
    assert probe.check_code("theorem t : True := by trivial", cwd=project, generation="built-v1")["ok"]
    opened = probe.proof_state_from_code("theorem t : True := by sorry", cwd=project, generation="built-v1")
    assert opened["generation"] == "built-v1"
    assert probe.check_target(other_target, theorem_id="second", cwd=other, generation="other-v1")["ok"]

    changed = probe.check_target(target, theorem_id="second", cwd=project, generation="built-v2")
    assert changed["ok"] and changed["cache"]["cache_hit"] is False
    assert changed["cache"]["generation"] == "built-v2"
    assert [server.killed for server in state["servers"]] == [True, True, True, False, False]
    assert probe.tactic_step(opened["session_id"], 5, "trivial")["error_code"] == "unknown_session"
    assert len(state["servers"][-1].runs) == 3  # rebuilt header and prefix on a new server


def test_first_explicit_generation_closes_previous_unversioned_workers(fake_backend, lean_project):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    warmed = probe.prepare_file(target, theorem_id="second", cwd=project, generation="prepared")
    assert warmed["ok"] and state["servers"][0].killed
    assert warmed["cache"]["generation"] == "prepared"
    omitted = probe.feedback(target, theorem_id="second", cwd=project)
    assert omitted["ok"] and omitted["cache"]["generation"] == "prepared"
    assert omitted["cache"]["cache_hit"] is True
    assert len(state["servers"]) == 2


def test_generation_change_from_scratch_or_status_invalidates_file_workers(fake_backend, lean_project):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    checked = probe.check_code("theorem t : True := by trivial", cwd=project, generation="v2")
    assert checked["cache"]["generation"] == "v2" and state["servers"][0].killed
    status = probe.capabilities(project, warm=True, generation="v3")
    assert status["generation"] == "v3" and state["servers"][1].killed
    assert status["active_sessions"] == []
    assert status["scratch_sessions"] == [str(project)]


def test_dead_server_reconstruction_preserves_generation_without_reusing_env_ids(fake_backend, lean_project):
    state = fake_backend(first_server_dies_on_env=True)
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    checked = probe.check_target(target, theorem_id="second", cwd=project, generation="built-v1")
    assert checked["ok"] and checked["cache"]["generation"] == "built-v1"
    assert len(state["servers"]) == 2
    assert state["servers"][0].killed
    assert len(state["servers"][1].runs) == 3


def test_close_forgets_generation_and_closes_every_worker(fake_backend, lean_project):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    assert probe.check_code("theorem t : True := by trivial", cwd=project)["ok"]
    assert probe.proof_state_from_code("theorem t : True := by sorry", cwd=project)["ok"]
    probe.close()
    assert all(server.killed for server in state["servers"])
    assert probe._project_generations == {}
    assert probe.capabilities(project)["generation"] is None


def _fail_kill():
    raise RuntimeError("termination permission denied")


@pytest.mark.parametrize("pool", ["file", "scratch", "proof_state"])
def test_eviction_failure_keeps_owner_and_blocks_new_worker(fake_backend, tmp_path, monkeypatch, pool):
    state = fake_backend()
    project, target = _project(tmp_path, "A")
    other, other_target = _project(tmp_path, "B")
    probe = LeanProbe(max_file_sessions=1, max_scratch_sessions=1, max_code_sessions=1)
    if pool == "file":
        original = probe.check_target(target, theorem_id="second", cwd=project)
        owners = probe._sessions
    elif pool == "scratch":
        original = probe.check_code("theorem t : True := by trivial", cwd=project)
        owners = probe._scratch_sessions
    else:
        original = probe.proof_state_from_code("theorem t : True := by sorry", cwd=project)
        owners = probe._code_sessions
    assert original["ok"]
    original_items = list(owners.items())
    monkeypatch.setattr(state["servers"][0], "kill", _fail_kill)

    if pool == "file":
        failed = probe.check_target(other_target, theorem_id="second", cwd=other)
    elif pool == "scratch":
        failed = probe.check_code("theorem t : True := by trivial", cwd=other)
    else:
        failed = probe.proof_state_from_code("theorem t : True := by sorry", cwd=other)
    assert failed["success"] is False and failed["error_code"] == "session_cleanup_failed"
    assert "termination permission denied" in failed["error"]
    assert list(owners.items()) == original_items
    assert len(state["servers"]) == 1


def test_close_project_failure_preserves_generation_and_uncleared_pool_ownership(
    fake_backend, lean_project, monkeypatch
):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    assert probe.check_code("theorem t : True := by trivial", cwd=project)["ok"]
    file_owner = list(probe._sessions.items())
    scratch_owner = list(probe._scratch_sessions.items())
    monkeypatch.setattr(state["servers"][0], "kill", _fail_kill)
    closed = probe.close_project(project)
    assert closed["success"] is False and closed["error_code"] == "session_cleanup_failed"
    assert closed["closed_sessions"] == {"file": 0, "scratch": 0, "proof_state": 0}
    assert probe._project_generations[str(project)] == "v1"
    assert list(probe._sessions.items()) == file_owner
    assert list(probe._scratch_sessions.items()) == scratch_owner
    assert state["servers"][1].killed is False


@pytest.mark.parametrize("operation", ["target", "prepare", "feedback", "code", "state", "status"])
def test_generation_change_failure_never_advances_generation_or_runs_new_check(
    fake_backend, lean_project, monkeypatch, operation
):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    original_run_count = len(state["servers"][0].runs)
    monkeypatch.setattr(state["servers"][0], "kill", _fail_kill)
    if operation in ("target", "prepare", "feedback"):
        method = {"target": probe.check_target, "prepare": probe.prepare_file, "feedback": probe.feedback}[operation]
        failed = method(target, theorem_id="second", cwd=project, generation="v2")
    elif operation == "status":
        failed = probe.capabilities(project, warm=True, generation="v2")
        assert failed["available"] is False and failed["generation"] == "v1"
    else:
        method = probe.check_code if operation == "code" else probe.proof_state_from_code
        failed = method("theorem t : True := by sorry", cwd=project, generation="v2")
    assert failed["success"] is False and failed["error_code"] == "session_cleanup_failed"
    assert probe._project_generations[str(project)] == "v1"
    assert len(probe._sessions) == 1
    assert len(state["servers"]) == 1 and len(state["servers"][0].runs) == original_run_count


def test_retirement_verifies_original_process_after_backend_clears_handle(fake_backend, lean_project, monkeypatch):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    server = state["servers"][0]

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

    process = Process()
    server._proc = process
    monkeypatch.setattr(server, "is_alive", lambda: server._proc is not None and server._proc.poll() is None)

    def swallowed_failure():
        server._proc = None  # Model LeanInteract's unconditional handle clearing.

    monkeypatch.setattr(server, "kill", swallowed_failure)
    for _ in range(2):
        failed = probe.close_project(project)
        assert failed["success"] is False and "owned REPL process is still alive" in failed["error"]
        assert len(probe._sessions) == 1
        assert next(iter(probe._sessions.values())).retirement_process is process
        assert probe._project_generations[str(project)] == "v1"
        retried = probe.check_target(target, theorem_id="second", cwd=project)
        assert retried["success"] is False and retried["error_code"] == "session_cleanup_failed"
        assert len(state["servers"]) == 1  # No fallthrough to unverified dead-server replacement.
    process.returncode = 0
    assert probe.close_project(project)["ok"]
    assert probe._sessions == {} and str(project) not in probe._project_generations


def test_unverifiable_successful_kill_is_not_retirement(fake_backend, lean_project, monkeypatch):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    monkeypatch.setattr(state["servers"][0], "is_alive", None)
    failed = probe.close_project(project)
    assert failed["success"] is False and "cannot be verified" in failed["error"]
    assert len(probe._sessions) == 1


def test_already_dead_race_with_kill_exception_retires_successfully(fake_backend, lean_project, monkeypatch):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    server = state["servers"][0]
    server.killed = True
    monkeypatch.setattr(server, "kill", _fail_kill)
    closed = probe.close_project(project)
    assert closed["ok"] and closed["closed_sessions"]["file"] == 1
    assert probe._sessions == {}


def test_live_worker_after_nonraising_kill_is_not_retirement(fake_backend, lean_project, monkeypatch):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    monkeypatch.setattr(state["servers"][0], "kill", lambda: None)
    failed = probe.close_project(project)
    assert failed["success"] is False and "backend worker is still alive" in failed["error"]
    assert len(probe._sessions) == 1


def test_warm_status_eviction_failure_reports_unavailable(fake_backend, tmp_path, monkeypatch):
    state = fake_backend()
    project, _ = _project(tmp_path, "A")
    other, _ = _project(tmp_path, "B")
    probe = LeanProbe(max_scratch_sessions=1)
    assert probe.capabilities(project, warm=True)["warmed"]
    monkeypatch.setattr(state["servers"][0], "kill", _fail_kill)
    failed = probe.capabilities(other, warm=True)
    assert failed["available"] is False and failed["warmed"] is False
    assert failed["success"] is False and failed["error_code"] == "session_cleanup_failed"
    assert len(state["servers"]) == 1 and list(probe._scratch_sessions) == [str(project)]


@pytest.mark.parametrize("failed_pool", ["file", "scratch", "proof_state"])
def test_partial_project_retirement_counts_only_verified_workers(fake_backend, lean_project, monkeypatch, failed_pool):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    assert probe.check_code("theorem t : True := by trivial", cwd=project)["ok"]
    opened = probe.proof_state_from_code("theorem t : True := by sorry", cwd=project)
    failed_index = {"file": 0, "scratch": 1, "proof_state": 2}[failed_pool]
    monkeypatch.setattr(state["servers"][failed_index], "kill", _fail_kill)
    result = probe.close_project(project)
    assert result["success"] is False and result["error_code"] == "session_cleanup_failed"
    assert result["closed_sessions"] == {
        "file": int(failed_index > 0),
        "scratch": int(failed_index > 1),
        "proof_state": 0,
    }
    assert len(probe._sessions) == int(failed_index == 0)
    assert len(probe._scratch_sessions) == int(failed_index <= 1)
    assert opened["session_id"] in probe._code_sessions
    assert probe._project_generations[str(project)] == "v1"
    if failed_pool == "proof_state":
        stepped = probe.tactic_step(opened["session_id"], 5, "trivial")
        assert stepped["success"] is False and stepped["error_code"] == "session_cleanup_failed"
        closed = probe.close_state(opened["session_id"])
        assert closed["success"] is False and closed["error_code"] == "session_cleanup_failed"
        assert opened["session_id"] in probe._code_sessions


def test_owned_live_child_prevents_project_retirement(fake_backend, lean_project):
    fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    owner = next(iter(probe._sessions.values()))

    class Process:
        def poll(self):
            return 0

    class Child:
        alive = True

        def is_running(self):
            return self.alive

        def status(self):
            return "running"

    child = Child()
    owner.retirement_process = Process()
    owner.retirement_children = [child]
    blocked = probe.close_project(project)
    assert blocked["success"] is False and "owned child process is still alive" in blocked["error"]
    assert next(iter(probe._sessions.values())) is owner
    child.alive = False
    assert probe.close_project(project)["ok"]


def test_global_close_retains_previously_quarantined_worker_and_generation(fake_backend, lean_project, monkeypatch):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    monkeypatch.setattr(state["servers"][0], "kill", _fail_kill)
    assert probe.close_project(project)["success"] is False
    probe.close()
    assert len(probe._sessions) == 1 and probe._project_generations[str(project)] == "v1"
    blocked = probe.check_target(target, theorem_id="second", cwd=project)
    assert blocked["success"] is False and blocked["error_code"] == "session_cleanup_failed"
    assert len(state["servers"]) == 1
    state["servers"][0].killed = True
    probe.close()
    assert probe._sessions == {} and probe._project_generations == {}


def test_global_close_preserves_legacy_best_effort_for_ordinary_worker(fake_backend, lean_project, monkeypatch):
    state = fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project)["ok"]
    monkeypatch.setattr(state["servers"][0], "kill", _fail_kill)
    probe.close()
    assert probe._sessions == {}  # Legacy close() behavior is unchanged until verified retirement fails.


def test_retirement_waits_for_transient_root_and_child_exit(fake_backend, lean_project, monkeypatch):
    import psutil

    fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    owner = next(iter(probe._sessions.values()))
    calls = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            calls.append(("root", timeout))
            assert 0 <= timeout <= sessions.RETIREMENT_WAIT_S
            self.returncode = 0
            return 0

    class Child:
        alive = True

        def is_running(self):
            return self.alive

        def status(self):
            return "running"

    child = Child()
    owner.retirement_process = Process()
    owner.retirement_children = [child]
    ticks = iter([1.0, 1.1, 1.4])
    monkeypatch.setattr(sessions.time, "perf_counter", lambda: next(ticks))

    def wait_children(children, *, timeout):
        calls.append(("children", timeout))
        assert children == [child] and 0 <= timeout <= sessions.RETIREMENT_WAIT_S
        child.alive = False
        return children, []

    monkeypatch.setattr(psutil, "wait_procs", wait_children)
    closed = probe.close_project(project)
    assert closed["ok"] and closed["closed_sessions"]["file"] == 1
    assert calls == [("root", pytest.approx(0.9)), ("children", pytest.approx(0.6))]
    assert owner.retirement_error == "" and probe._sessions == {}


def test_retirement_wait_timeout_keeps_worker_quarantined(fake_backend, lean_project, monkeypatch):
    fake_backend()
    project, target = lean_project(SOURCE)
    probe = LeanProbe()
    assert probe.check_target(target, theorem_id="second", cwd=project, generation="v1")["ok"]
    owner = next(iter(probe._sessions.values()))
    calls = []

    class Process:
        def poll(self):
            return None

        def wait(self, *, timeout):
            calls.append(timeout)
            assert 0 <= timeout <= sessions.RETIREMENT_WAIT_S
            raise TimeoutError("still alive")

    owner.retirement_process = Process()
    owner.retirement_children = []
    failed = probe.close_project(project)
    assert failed["success"] is False and failed["error_code"] == "session_cleanup_failed"
    assert calls and next(iter(probe._sessions.values())) is owner
    assert owner.retirement_error and probe._project_generations[str(project)] == "v1"
