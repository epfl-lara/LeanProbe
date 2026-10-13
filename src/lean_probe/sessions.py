"""LeanInteract session lifecycle and the low-level command runner.

``run_command`` runs exactly one REPL command and classifies failures; it does
NOT retry. Dead-server recovery is the orchestrator's job (see ``probe.py``),
because recovering an environment-bearing command requires rebuilding the cached
environment on the fresh REPL — replaying a stale ``env`` id against a restarted
server is a silent correctness bug.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ErrorCode, _error_code_for_exception
from .transport import server_class_for_transport

RETIREMENT_WAIT_S = 1.0


def _import_lean_interact() -> tuple[Any, Any, Any, Any, Any, str]:
    try:
        from lean_interact import Command, LeanREPLConfig, LeanServer, LocalProject, ProofStep
    except Exception as exc:
        return None, None, None, None, None, f"lean-interact unavailable: {exc}"
    return Command, ProofStep, LeanREPLConfig, server_class_for_transport(LeanServer), LocalProject, ""


def import_error() -> str:
    """Return the LeanInteract import error, or '' when the backend is importable."""

    return _import_lean_interact()[5]


def get_proof_step() -> tuple[Any, str]:
    """Return ``(ProofStep, error)`` for tactic stepping."""

    _, ProofStep, _, _, _, error = _import_lean_interact()
    return ProofStep, error


@dataclass
class _Checkpoint:
    before_env: int | None
    after_env: int | None
    text_hash: str


@dataclass
class _IncrementalSession:
    project_root: Path
    file_path: Path
    repl_dir: Path | None
    server: Any
    config: Any
    header_hash: str = ""
    header_env: int | None = None
    # Keyed by segment index; reuse is gated on matching (before_env, text_hash),
    # and Lean elaboration is deterministic, so a matching pair is always a valid
    # cache hit regardless of how indices shift across edits.
    checkpoints: dict[int, _Checkpoint] = field(default_factory=dict)
    retirement_process: Any = None
    retirement_children: list[Any] | None = None
    retirement_error: str = ""

    def close(self) -> None:
        try:
            self.server.kill()
        except Exception:
            pass


@dataclass
class _CodeSession:
    server: Any
    config: Any
    cwd: Path | None
    retirement_process: Any = None
    retirement_children: list[Any] | None = None
    retirement_error: str = ""

    def close(self) -> None:
        try:
            self.server.kill()
        except Exception:
            pass


def retire_session(session: _IncrementalSession | _CodeSession) -> str:
    """Kill a worker and verify its captured process handles before releasing ownership.

    LeanInteract may clear ``_proc`` even if killing fails. Keep the original Popen
    and child handles on the session so a later attempt cannot mistake that worker
    for an already retired server. Backends without process handles must provide a
    usable ``is_alive`` method; a successful kill alone is not proof of exit.
    """

    try:
        error = _retirement_error(session)
    except Exception as exc:
        error = f"session cleanup failed: backend exit cannot be verified: {exc}"
    session.retirement_error = error
    return error


def _retirement_error(session: _IncrementalSession | _CodeSession) -> str:
    """Return a retirement failure while preserving handles for later verified cleanup."""

    if session.retirement_process is None:
        session.retirement_process = getattr(session.server, "_proc", None)
    process = session.retirement_process
    if process is not None and session.retirement_children is None:
        session.retirement_children = []
        if getattr(process, "pid", None) is not None:
            try:
                import psutil
            except ImportError as exc:
                session.retirement_children = None
                return f"session cleanup failed: cannot inspect owned child processes: {exc}"
            try:
                session.retirement_children = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                pass  # The root exited between capture and process-tree inspection.
            except Exception as exc:
                session.retirement_children = None
                return f"session cleanup failed: cannot inspect owned child processes: {exc}"
    kill_error = ""
    try:
        session.server.kill()
    except Exception as exc:
        kill_error = str(exc)
    try:
        if process is not None:
            _wait_for_retirement(process, session.retirement_children or [])
            if process.poll() is None:
                return "session cleanup failed: owned REPL process is still alive" + (
                    f" ({kill_error})" if kill_error else ""
                )
            for child in session.retirement_children or []:
                try:
                    if child.is_running() and child.status() != "zombie":
                        return "session cleanup failed: owned child process is still alive"
                except ProcessLookupError:
                    continue
                except Exception as exc:
                    # psutil.NoSuchProcess is not a ProcessLookupError on all versions.
                    import psutil

                    if isinstance(exc, psutil.NoSuchProcess):
                        continue
                    raise
        else:
            is_alive = getattr(session.server, "is_alive", None)
            if not callable(is_alive):
                return "session cleanup failed: backend exit cannot be verified" + (
                    f" ({kill_error})" if kill_error else ""
                )
            alive = is_alive()
            if alive is not True and alive is not False:
                return "session cleanup failed: backend exit cannot be verified"
            if alive:
                return "session cleanup failed: backend worker is still alive" + (
                    f" ({kill_error})" if kill_error else ""
                )
    except Exception as exc:
        return f"session cleanup failed: backend exit cannot be verified: {exc}"
    return ""


def _wait_for_retirement(process: Any, children: list[Any]) -> None:
    """Allow signalled processes to exit within one shared, bounded grace period.

    LeanInteract may issue SIGKILL to surviving children immediately before returning.
    Waiting prevents transient liveness from becoming a sticky cleanup failure; only
    the subsequent exit checks decide whether retirement succeeded.
    """

    deadline = time.perf_counter() + RETIREMENT_WAIT_S
    if process.poll() is None:
        wait = getattr(process, "wait", None)
        if callable(wait):
            try:
                wait(timeout=max(0.0, deadline - time.perf_counter()))
            except Exception:
                pass  # Verify the actual exit state below, including timeout/death races.
    if children:
        try:
            import psutil

            live_children = []
            for child in children:
                try:
                    if child.is_running() and child.status() != "zombie":
                        live_children.append(child)
                except (ProcessLookupError, psutil.NoSuchProcess):
                    continue
            if live_children:
                psutil.wait_procs(live_children, timeout=max(0.0, deadline - time.perf_counter()))
        except Exception:
            pass  # Waiting is an optimization; final liveness verification remains mandatory.


def build_server(
    *,
    project_root: Path | None,
    repl_dir: Path | None,
    lake_path: str | Path,
    auto_build: bool,
    verbose: bool,
) -> tuple[Any | None, Any | None, str]:
    """Start a LeanServer; return ``(server, config, error)``.

    ``project_root=None`` builds a project-less server (standalone snippets).
    """

    _, _, LeanREPLConfig, LeanServer, LocalProject, import_error = _import_lean_interact()
    if LeanREPLConfig is None or LeanServer is None or LocalProject is None:
        return None, None, import_error
    try:
        kwargs: dict[str, Any] = {"lake_path": str(lake_path), "verbose": verbose}
        if project_root is not None:
            kwargs["project"] = LocalProject(directory=str(project_root), auto_build=auto_build)
        if repl_dir is not None:
            kwargs.update({"local_repl_path": str(repl_dir), "build_repl": False})
        config = LeanREPLConfig(**kwargs)
        server = LeanServer(config)
    except Exception as exc:
        return None, None, f"failed to start LeanInteract server: {exc}"
    return server, config, ""


def run_command(
    server: Any,
    cmd: str,
    *,
    env: int | None,
    include_tactics: bool,
    timeout_s: int,
) -> tuple[Any | None, float, str, str, bool]:
    """Run one REPL command. Returns ``(response, elapsed_s, error, error_code, timed_out)``."""

    Command, _, _, _, _, import_error = _import_lean_interact()
    if Command is None:
        return None, 0.0, import_error, ErrorCode.LEAN_INTERACT_UNAVAILABLE, False
    start = time.perf_counter()
    try:
        request = Command(cmd=cmd, all_tactics=True) if include_tactics else Command(cmd=cmd)
        if env is not None:
            request = request.model_copy(update={"env": env})
        response = server.run(request, timeout=timeout_s)
    except Exception as exc:
        error_code = _error_code_for_exception(exc)
        return None, time.perf_counter() - start, str(exc), error_code, error_code == ErrorCode.TIMEOUT
    return response, time.perf_counter() - start, "", "", False
