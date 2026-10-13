"""Shared test fixtures: an in-memory fake LeanInteract backend and project maker.

The fake models per-server environment tables: each LeanServer instance owns the
env ids it issues, and running a command against an env id the current server
never issued raises — so replaying a *stale* env against a restarted REPL is
caught instead of silently passing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from lean_probe import projects, sessions


def _build_backend(*, first_server_dies_on_env: bool = False) -> tuple[tuple[Any, ...], dict[str, Any]]:
    state: dict[str, Any] = {"next_env": 0, "servers": []}

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
            self.messages: list[Any] = []
            self.sorries = sorries or []
            self.tactics = tactics or []
            self._errors = errors
            if errors:
                pos = SimpleNamespace(line=1, column=7)
                self.messages.append(
                    SimpleNamespace(severity="error", data="unexpected token", start_pos=pos, end_pos=pos)
                )

        def has_errors(self):
            return self._errors

        def lean_code_is_valid(self, *, allow_sorry: bool = False):
            return not self._errors and (allow_sorry or not self.sorries)

    class _StepResponse:
        proof_state = 77
        goals: list[Any] = []
        proof_status = "Completed"

    class _Server:
        def __init__(self, config):
            self.config = config
            self.runs: list[dict[str, Any]] = []
            self.owned: set[int] = set()
            self.killed = False
            self.index = len(state["servers"])
            state["servers"].append(self)

        def _new_env(self) -> int:
            env = state["next_env"]
            state["next_env"] += 1
            self.owned.add(env)
            return env

        def run(self, request, timeout=None):
            if isinstance(request, _ProofStep):
                self.runs.append({"proof_state": request.proof_state, "tactic": request.tactic, "timeout": timeout})
                return _StepResponse()
            env = request.env
            self.runs.append({"cmd": request.cmd, "env": env, "all_tactics": request.all_tactics, "timeout": timeout})
            if first_server_dies_on_env and self.index == 0 and env is not None:
                raise RuntimeError("The Lean server is not running.")
            if env is not None and env not in self.owned:
                raise RuntimeError(f"unknown environment id {env!r} on this server")
            cmd = request.cmd
            if "by sorry" in cmd or cmd.strip().endswith("sorry"):
                pos = SimpleNamespace(line=1, column=40)
                sorry = SimpleNamespace(start_pos=pos, end_pos=pos, goal="n : Nat\n⊢ n = n", proof_state=5)
                return _Response(env=self._new_env(), sorries=[sorry])
            if "bad" in cmd:
                return _Response(env=self._new_env(), errors=True)
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
            return _Response(env=self._new_env(), tactics=tactics)

        def kill(self):
            self.killed = True

        def is_alive(self):
            return not self.killed

    class _Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Project:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    return (_Command, _ProofStep, _Config, _Server, _Project), state


@pytest.fixture
def fake_backend(monkeypatch):
    """Install the fake backend; returns a callable -> shared state dict."""

    def _install(*, first_server_dies_on_env: bool = False) -> dict[str, Any]:
        (Command, ProofStep, Config, Server, Project), state = _build_backend(
            first_server_dies_on_env=first_server_dies_on_env
        )
        monkeypatch.setattr(
            sessions, "_import_lean_interact", lambda: (Command, ProofStep, Config, Server, Project, "")
        )
        monkeypatch.setattr(projects, "_local_repl_dir", lambda root: root / ".lake" / "packages" / "repl")
        return state

    return _install


@pytest.fixture
def lean_project(tmp_path):
    """Return a callable that writes a Demo Lake project and returns (project, target)."""

    def _make(text: str):
        project = tmp_path / "Demo"
        module_dir = project / "Demo"
        module_dir.mkdir(parents=True, exist_ok=True)
        (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
        target = module_dir / "Main.lean"
        target.write_text(text, encoding="utf-8")
        return project, target

    return _make
