"""LeanProbe: a reusable LeanInteract-backed checker for serial agent proof loops.

Public API (stable): ``capabilities``, ``check_code``, ``prepare_file``,
``check_target``, ``feedback``, ``proof_state_from_code``, ``tactic_step``,
``close_state``, ``close_project``, ``close``.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from . import projects, sessions
from .errors import (
    ErrorCode,
    _error_code_for_exception,
    _error_code_for_message,
    _timeout_error_text,
    hint_for_code,
)
from .payloads import (
    BACKEND,
    TOOL,
    _feedback_lean,
    _format_message_summary,
    _message_payloads,
    _sorry_payloads,
    _tactic_payloads,
    error_envelope,
    outcome,
    response_payload,
)
from .projects import find_lean_project_root
from .segmentation import (
    LeanIncrementalSegment,
    _find_segment,
    _mutual_target_hint,
    _normalize_inline_scoped_declarations,
    _sha,
    segment_file,
)
from .sessions import (
    _Checkpoint,
    _CodeSession,
    _IncrementalSession,
    build_server,
    run_command,
)

DEFAULT_MAX_CODE_SESSIONS = 16
DEFAULT_MAX_FILE_SESSIONS = 4
DEFAULT_MAX_SCRATCH_SESSIONS = 2
# Matches the start of a Lean declaration; used to reject bare proof bodies passed
# as `replacement` (the most common agent footgun).
_DECLARATION_KEYWORD = re.compile(
    r"(?:^|\n)\s*(?:@\[|(?:private|protected|noncomputable|partial|unsafe|nonrec|scoped|local)\s+)*"
    r"(?:theorem|lemma|example|def|instance|class|structure|inductive|abbrev|axiom|opaque|mutual)\b"
)


def _looks_like_declaration(text: str) -> bool:
    return bool(_DECLARATION_KEYWORD.search(_normalize_inline_scoped_declarations(str(text or ""))))


class LeanProbe:
    """Reusable LeanInteract-backed checker for serialized agent proof loops."""

    def __init__(
        self,
        *,
        auto_build: bool = False,
        local_repl_path: str | Path | None = None,
        lake_path: str | Path = "lake",
        verbose: bool = False,
        max_code_sessions: int = DEFAULT_MAX_CODE_SESSIONS,
        max_file_sessions: int = DEFAULT_MAX_FILE_SESSIONS,
        max_scratch_sessions: int = DEFAULT_MAX_SCRATCH_SESSIONS,
    ) -> None:
        self.auto_build = auto_build
        self.local_repl_path = Path(local_repl_path).expanduser().resolve() if local_repl_path else None
        self.lake_path = Path(lake_path)
        self.verbose = verbose
        self._sessions: OrderedDict[tuple[str, str], _IncrementalSession] = OrderedDict()
        self._code_sessions: OrderedDict[str, _CodeSession] = OrderedDict()
        self._scratch_sessions: OrderedDict[str, _CodeSession] = OrderedDict()
        self._project_generations: dict[str, str] = {}
        self.max_code_sessions = max(1, int(max_code_sessions))
        self.max_file_sessions = max(1, int(max_file_sessions))
        self.max_scratch_sessions = max(1, int(max_scratch_sessions))
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            # Preserve legacy best-effort shutdown for ordinary workers. A worker
            # already quarantined by verified retirement must retain ownership even
            # across close(), or a later call could allocate over an orphan process.
            pools: tuple[OrderedDict[Any, Any], ...] = (self._sessions, self._code_sessions, self._scratch_sessions)
            for pool in pools:
                for key, session in list(pool.items()):
                    if session.retirement_error:
                        if sessions.retire_session(session):
                            continue
                    else:
                        session.close()
                    pool.pop(key)
            retained_projects = {key[0] for key in self._sessions}
            retained_projects.update(self._scratch_sessions)
            retained_projects.update(str(session.cwd or "") for session in self._code_sessions.values())
            self._project_generations = {
                key: generation for key, generation in self._project_generations.items() if key in retained_projects
            }

    def close_project(self, cwd: str | Path) -> dict[str, Any]:
        """Close only this project's workers and forget its caller-supplied generation."""

        with self._lock:
            project_root = self._resolve_project_root(cwd)
            if project_root is None:
                return error_envelope(
                    action="close_project",
                    error="Lean project root not detected",
                    error_code=ErrorCode.NO_PROJECT_ROOT,
                    searched=self._root_search_candidates(cwd),
                )
            counts, error = self._close_project_sessions(project_root)
            if error:
                return error_envelope(
                    action="close_project",
                    error=error,
                    error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                    project_root=project_root,
                    closed_sessions=counts,
                )
            self._project_generations.pop(str(project_root), None)
            return {
                "success": True,
                "ok": True,
                "backend": BACKEND,
                "tool": TOOL,
                "action": "close_project",
                "project_root": str(project_root),
                "closed_sessions": counts,
            }

    def _close_project_sessions(self, project_root: Path | None) -> tuple[dict[str, int], str]:
        """Discard all environment families for one resolved project, retaining other projects."""

        key = str(project_root or "")
        counts = {"file": 0, "scratch": 0, "proof_state": 0}
        for session_key in list(self._sessions):
            if session_key[0] == key:
                error = sessions.retire_session(self._sessions[session_key])
                if error:
                    return counts, error
                self._sessions.pop(session_key)
                counts["file"] += 1
        scratch = self._scratch_sessions.get(key)
        if scratch is not None:
            error = sessions.retire_session(scratch)
            if error:
                return counts, error
            self._scratch_sessions.pop(key)
            counts["scratch"] += 1
        for session_id, code_session in list(self._code_sessions.items()):
            if code_session.cwd == project_root:
                error = sessions.retire_session(code_session)
                if error:
                    return counts, error
                self._code_sessions.pop(session_id)
                counts["proof_state"] += 1
        return counts, ""

    def _activate_generation(self, project_root: Path | None, generation: str | None) -> tuple[str | None, str]:
        """Invalidate this project's workers when its caller-owned identity changes.

        No dependency files are inspected or built here. Callers must change the token
        after preparing changed imports, build options, or toolchain artifacts. Omitting
        the token retains the active identity for compatibility with unversioned callers.
        """

        key = str(project_root or "")
        if generation is not None and self._project_generations.get(key) != generation:
            _counts, error = self._close_project_sessions(project_root)
            if error:
                return self._project_generations.get(key), error
            self._project_generations[key] = generation
        # A failed retirement may have cleared backend handles while the process
        # remains alive. Quarantine it so an old-token retry cannot bypass verified
        # cleanup through the legacy dead-server reconstruction path.
        for session_key, file_session in self._sessions.items():
            if session_key[0] == key and file_session.retirement_error:
                return self._project_generations.get(key), file_session.retirement_error
        scratch = self._scratch_sessions.get(key)
        if scratch is not None and scratch.retirement_error:
            return self._project_generations.get(key), scratch.retirement_error
        for code_session in self._code_sessions.values():
            if code_session.cwd == project_root and code_session.retirement_error:
                return self._project_generations.get(key), code_session.retirement_error
        return self._project_generations.get(key), ""

    # ------------------------------------------------------------------ status

    def capabilities(
        self, cwd: str | Path | None = None, *, warm: bool = False, generation: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            import_error = sessions.import_error()
            project_root = self._resolve_project_root(cwd)
            active_generation, generation_error = (
                self._activate_generation(project_root, generation) if project_root is not None else (None, "")
            )
            repl_dir = self._select_repl_dir(project_root) if project_root else None
            degraded: list[str] = []
            degraded_codes: list[str] = []
            if import_error:
                degraded.append(import_error)
                degraded_codes.append(ErrorCode.LEAN_INTERACT_UNAVAILABLE)
            if project_root is None:
                degraded.append("Lean project root not detected")
                degraded_codes.append(ErrorCode.NO_PROJECT_ROOT)
            if generation_error:
                degraded.append(generation_error)
                degraded_codes.append(ErrorCode.SESSION_CLEANUP_FAILED)
            warmed = False
            warm_error = ""
            if warm and project_root is not None and not import_error and not generation_error:
                session, warm_error = self._get_scratch_session(project_root)
                warmed = session is not None
                if warm_error:
                    degraded.append(warm_error)
                    warm_error_code = _error_code_for_message(warm_error)
                    degraded_codes.append(warm_error_code)
                    if warm_error_code == ErrorCode.SESSION_CLEANUP_FAILED:
                        generation_error = warm_error
            payload = {
                "available": not import_error and bool(project_root) and not generation_error,
                "tool": TOOL,
                "backend": BACKEND,
                "project_root": str(project_root or ""),
                "repl_dir": str(repl_dir or ""),
                "warmed": warmed,
                "active_sessions": [
                    {"project_root": project, "file": file_path} for project, file_path in self._sessions.keys()
                ],
                "code_sessions": list(self._code_sessions.keys()),
                "max_code_sessions": self.max_code_sessions,
                "scratch_sessions": list(self._scratch_sessions.keys()),
                "max_file_sessions": self.max_file_sessions,
                "max_scratch_sessions": self.max_scratch_sessions,
                "generation": active_generation,
                "degraded_reasons": degraded,
                "degraded_codes": degraded_codes,
                "hint": hint_for_code(ErrorCode.NO_PROJECT_ROOT, searched=self._root_search_candidates(cwd))
                if project_root is None
                else "",
            }
            if generation_error:
                payload.update(
                    error_envelope(
                        action="status",
                        error=generation_error,
                        error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                        project_root=project_root,
                    )
                )
            return payload

    # --------------------------------------------------------------- snippet check

    def check_code(
        self,
        code: str,
        *,
        cwd: str | Path | None = None,
        include_tactics: bool = False,
        timeout_s: int = 90,
        generation: str | None = None,
    ) -> dict[str, Any]:
        """Check a standalone Lean snippet (imports + declarations) and report diagnostics."""

        with self._lock:
            project_root = self._resolve_project_root(cwd)
            if cwd and project_root is None:
                return error_envelope(
                    action="check",
                    error="Lean project root not detected",
                    error_code=ErrorCode.NO_PROJECT_ROOT,
                    searched=self._root_search_candidates(cwd),
                    elapsed_s=0.0,
                )
            active_generation, generation_error = self._activate_generation(project_root, generation)
            if generation_error:
                return error_envelope(
                    action="check",
                    error=generation_error,
                    error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                    project_root=project_root,
                    elapsed_s=0.0,
                )
            response = None
            elapsed = 0.0
            error = ""
            error_code = ""
            timed_out = False
            reused = False
            for attempt in (0, 1):
                session, build_error = self._get_scratch_session(project_root)
                if session is None:
                    return error_envelope(
                        action="check",
                        error=build_error,
                        error_code=_error_code_for_message(build_error),
                        project_root=project_root,
                        elapsed_s=0.0,
                    )
                reused = attempt == 0 and self._scratch_was_reused
                response, elapsed, error, error_code, timed_out = run_command(
                    session.server, code, env=None, include_tactics=include_tactics, timeout_s=timeout_s
                )
                if error_code == ErrorCode.DEAD_SERVER and attempt == 0:
                    self._drop_scratch_session(project_root)
                    continue
                break
            if error:
                return error_envelope(
                    action="check",
                    error=error,
                    error_code=error_code,
                    timed_out=timed_out,
                    project_root=project_root,
                    elapsed_s=elapsed,
                )
            has_errors, has_sorry, valid_without_sorry = outcome(response)
            messages = _message_payloads(response)
            tactics = _tactic_payloads(response) if include_tactics else []
            sorries = _sorry_payloads(response)
            output = "\n".join(
                f"{m.get('severity', '')}: {m.get('message', '')}".strip()
                for m in messages
                if str(m.get("message", "") or "").strip()
            )
            return {
                "success": True,
                "ok": valid_without_sorry and not has_errors and not has_sorry,
                "backend": BACKEND,
                "tool": TOOL,
                "action": "check",
                "valid_without_sorry": valid_without_sorry,
                "has_errors": has_errors,
                "has_sorry": has_sorry,
                "timed_out": False,
                "error_code": "",
                "error": "",
                "elapsed_s": round(elapsed, 3),
                "command": "lean_probe check",
                "project_root": str(project_root or ""),
                "output": output,
                "messages": messages,
                "sorries": sorries,
                "tactics": tactics,
                "feedback_lean": _feedback_lean(code, messages, tactics) if (messages or tactics) else "",
                "cache": {"reused_server": reused, "generation": active_generation},
            }

    # --------------------------------------------------------------- file targets

    def prepare_file(
        self,
        file_path: str | Path,
        *,
        theorem_id: str = "",
        cwd: str | Path | None = None,
        timeout_s: int = 90,
        generation: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._check(
                action="prepare",
                file_path=file_path,
                theorem_id=theorem_id,
                cwd=cwd,
                replacement="",
                include_tactics=False,
                timeout_s=timeout_s,
                generation=generation,
            )

    def check_target(
        self,
        file_path: str | Path,
        *,
        theorem_id: str,
        cwd: str | Path | None = None,
        replacement: str = "",
        include_tactics: bool = False,
        timeout_s: int = 90,
        generation: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._check(
                action="check",
                file_path=file_path,
                theorem_id=theorem_id,
                cwd=cwd,
                replacement=replacement,
                include_tactics=include_tactics,
                timeout_s=timeout_s,
                generation=generation,
            )

    def feedback(
        self,
        file_path: str | Path,
        *,
        theorem_id: str,
        cwd: str | Path | None = None,
        replacement: str = "",
        timeout_s: int = 90,
        generation: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._check(
                action="feedback",
                file_path=file_path,
                theorem_id=theorem_id,
                cwd=cwd,
                replacement=replacement,
                include_tactics=True,
                timeout_s=timeout_s,
                generation=generation,
            )

    # --------------------------------------------------------------- proof states

    def proof_state_from_code(
        self,
        code: str,
        *,
        cwd: str | Path | None = None,
        include_tactics: bool = False,
        timeout_s: int = 90,
        generation: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            project_root = self._resolve_project_root(cwd)
            if cwd and project_root is None:
                return error_envelope(
                    action="state",
                    error="Lean project root not detected",
                    error_code=ErrorCode.NO_PROJECT_ROOT,
                    searched=self._root_search_candidates(cwd),
                    elapsed_s=0.0,
                )
            active_generation, generation_error = self._activate_generation(project_root, generation)
            if generation_error:
                return error_envelope(
                    action="state",
                    error=generation_error,
                    error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                    project_root=project_root,
                    elapsed_s=0.0,
                )
            cleanup_error = self._make_room(self._code_sessions, self.max_code_sessions)
            if cleanup_error:
                return error_envelope(
                    action="state",
                    error=cleanup_error,
                    error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                    project_root=project_root,
                    elapsed_s=0.0,
                )
            session_id = str(uuid.uuid4())
            session, error = self._new_code_session(project_root)
            if session is None:
                return error_envelope(
                    action="state",
                    error=error,
                    error_code=_error_code_for_message(error),
                    project_root=project_root,
                    elapsed_s=0.0,
                )
            self._remember_code_session(session_id, session)
            response = None
            elapsed = 0.0
            run_error = ""
            run_error_code = ""
            timed_out = False
            for attempt in (0, 1):
                response, elapsed, run_error, run_error_code, timed_out = run_command(
                    session.server, code, env=None, include_tactics=include_tactics, timeout_s=timeout_s
                )
                if run_error_code == ErrorCode.DEAD_SERVER and attempt == 0:
                    fresh, restart_error = self._restart_code_session(session_id)
                    if fresh is None:
                        run_error, run_error_code = restart_error, _error_code_for_message(restart_error)
                        break
                    session = self._code_sessions[session_id]
                    continue
                break
            messages = _message_payloads(response) if response is not None else []
            sorries = _sorry_payloads(response) if response is not None else []
            has_errors, _has_sorry_flag, _valid = outcome(response)
            if response is None and run_error:
                has_errors = True
            payload = {
                "success": not bool(run_error),
                "ok": not has_errors and bool(sorries),
                "backend": BACKEND,
                "tool": TOOL,
                "action": "state",
                "session_id": session_id,
                "env": getattr(response, "env", None) if response is not None else None,
                "has_errors": has_errors,
                "timed_out": timed_out,
                "error_code": run_error_code if run_error else "",
                "error": run_error,
                "elapsed_s": round(elapsed, 3),
                "project_root": str(project_root or ""),
                "generation": active_generation,
                "messages": messages,
                "sorries": sorries,
                "tactics": _tactic_payloads(response) if response is not None and include_tactics else [],
            }
            if run_error:
                payload["hint"] = hint_for_code(run_error_code)
            return payload

    def close_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._code_sessions.get(session_id)
            if session is None:
                return error_envelope(
                    action="close_state",
                    error="unknown LeanProbe proof session",
                    error_code=ErrorCode.UNKNOWN_SESSION,
                    session_id=session_id,
                )
            if session.retirement_error:
                error = sessions.retire_session(session)
                if error:
                    return error_envelope(
                        action="close_state",
                        error=error,
                        error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                        session_id=session_id,
                    )
            self._code_sessions.pop(session_id)
            session.close()
            return {
                "success": True,
                "ok": True,
                "backend": BACKEND,
                "tool": TOOL,
                "action": "close_state",
                "session_id": session_id,
            }

    def tactic_step(
        self,
        session_id: str,
        proof_state: int,
        tactic: str,
        *,
        timeout_s: int = 60,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._code_sessions.get(session_id)
            if session is None:
                return error_envelope(
                    action="step",
                    error="unknown LeanProbe proof session",
                    error_code=ErrorCode.UNKNOWN_SESSION,
                    session_id=session_id,
                )
            if session.retirement_error:
                return error_envelope(
                    action="step",
                    error=session.retirement_error,
                    error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                    session_id=session_id,
                )
            self._code_sessions.move_to_end(session_id)
            ProofStep, import_error = sessions.get_proof_step()
            if ProofStep is None:
                return error_envelope(
                    action="step",
                    error=import_error,
                    error_code=ErrorCode.LEAN_INTERACT_UNAVAILABLE,
                    session_id=session_id,
                )
            start = time.perf_counter()
            try:
                response = session.server.run(ProofStep(proof_state=proof_state, tactic=tactic), timeout=timeout_s)
                elapsed = time.perf_counter() - start
                status = str(getattr(response, "proof_status", "") or "")
                return {
                    "success": True,
                    "ok": status == "Completed",
                    "backend": BACKEND,
                    "tool": TOOL,
                    "action": "step",
                    "session_id": session_id,
                    "proof_state": getattr(response, "proof_state", None),
                    "goals": list(getattr(response, "goals", []) or []),
                    "proof_status": status,
                    "elapsed_s": round(elapsed, 3),
                }
            except Exception as exc:
                error_code = _error_code_for_exception(exc)
                session_dead = error_code == ErrorCode.DEAD_SERVER
                if session_dead:
                    stale = self._code_sessions.pop(session_id, None)
                    if stale is not None:
                        stale.close()
                return {
                    "success": False,
                    "ok": False,
                    "backend": BACKEND,
                    "tool": TOOL,
                    "action": "step",
                    "session_id": session_id,
                    "timed_out": error_code == ErrorCode.TIMEOUT,
                    "error_code": ErrorCode.SESSION_DEAD if session_dead else error_code,
                    "session_dead": session_dead,
                    "hint": hint_for_code(ErrorCode.SESSION_DEAD) if session_dead else hint_for_code(error_code),
                    "error": str(exc),
                    "elapsed_s": round(time.perf_counter() - start, 3),
                }

    # ------------------------------------------------------------- resolution

    def _root_search_candidates(self, cwd: str | Path | None, file_path: str | Path | None = None) -> list[str]:
        candidates: list[Path] = []
        if cwd:
            candidates.append(Path(cwd).expanduser().resolve())
        else:
            if file_path:
                path = Path(file_path).expanduser()
                candidates.append((path if path.is_dir() else path.parent).resolve())
            candidates.append(Path.cwd().resolve())
        return [str(c) for c in candidates]

    def _resolve_project_root(self, cwd: str | Path | None, file_path: str | Path | None = None) -> Path | None:
        if cwd:
            return find_lean_project_root(Path(cwd).expanduser().resolve())
        candidates: list[Path] = []
        if file_path:
            path = Path(file_path).expanduser()
            candidates.append((path if path.is_dir() else path.parent).resolve())
        candidates.append(Path.cwd().resolve())
        for candidate in candidates:
            root = find_lean_project_root(candidate)
            if root is not None:
                return root.resolve()
        return None

    def _resolve_file_path(self, file_path: str | Path, project_root: Path | None) -> Path:
        raw = Path(str(file_path or "")).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        if project_root is not None:
            return (project_root / raw).resolve()
        return raw.resolve()

    def _select_repl_dir(self, project_root: Path | None) -> Path | None:
        if self.local_repl_path is not None:
            return self.local_repl_path
        if project_root is not None:
            return projects._local_repl_dir(project_root)
        return None

    # ------------------------------------------------------------- sessions

    def _session_key(self, project_root: Path, file_path: Path) -> tuple[str, str]:
        return str(project_root.resolve()), str(file_path.resolve())

    def _new_session(
        self, project_root: Path, file_path: Path, repl_dir: Path | None
    ) -> tuple[_IncrementalSession | None, str]:
        server, config, error = build_server(
            project_root=project_root,
            repl_dir=repl_dir,
            lake_path=self.lake_path,
            auto_build=self.auto_build,
            verbose=self.verbose,
        )
        if server is None:
            return None, error
        return _IncrementalSession(
            project_root=project_root, file_path=file_path, repl_dir=repl_dir, server=server, config=config
        ), ""

    def _get_session(self, project_root: Path, file_path: Path) -> tuple[_IncrementalSession | None, str]:
        repl_dir = self._select_repl_dir(project_root)
        key = self._session_key(project_root, file_path)
        existing = self._sessions.get(key)
        if existing is not None and existing.retirement_error:
            return None, existing.retirement_error
        if existing and existing.repl_dir == repl_dir:
            self._sessions.move_to_end(key)
            return existing, ""
        if existing:
            error = sessions.retire_session(existing)
            if error:
                return None, error
            self._sessions.pop(key)
        error = self._make_room(self._sessions, self.max_file_sessions)
        if error:
            return None, error
        session, error = self._new_session(project_root, file_path, repl_dir)
        if session is not None:
            self._sessions[key] = session
        return session, error

    def _restart_session(self, session: _IncrementalSession) -> tuple[_IncrementalSession | None, str]:
        key = self._session_key(session.project_root, session.file_path)
        session.close()
        self._sessions.pop(key, None)
        new_session, error = self._new_session(session.project_root, session.file_path, session.repl_dir)
        if new_session is not None:
            self._sessions[key] = new_session
            session.__dict__.update(new_session.__dict__)
            self._sessions[key] = session
        return (session if new_session is not None else None), error

    def _new_code_session(self, project_root: Path | None) -> tuple[_CodeSession | None, str]:
        repl_dir = self._select_repl_dir(project_root)
        server, config, error = build_server(
            project_root=project_root,
            repl_dir=repl_dir,
            lake_path=self.lake_path,
            auto_build=self.auto_build,
            verbose=self.verbose,
        )
        if server is None:
            return None, error
        return _CodeSession(server=server, config=config, cwd=project_root), ""

    def _remember_code_session(self, session_id: str, session: _CodeSession) -> None:
        self._code_sessions[session_id] = session
        self._code_sessions.move_to_end(session_id)

    def _make_room(self, pool: OrderedDict[Any, Any], limit: int) -> str:
        """Retire LRU workers before allocation; retain failed retirements in their pool."""

        while len(pool) >= limit:
            key = next(iter(pool))
            error = sessions.retire_session(pool[key])
            if error:
                return error
            pool.pop(key)
        return ""

    def _restart_code_session(self, session_id: str) -> tuple[Any | None, str]:
        session = self._code_sessions.get(session_id)
        old_cwd = session.cwd if session is not None else None
        if session is not None:
            session.close()
        self._code_sessions.pop(session_id, None)
        new_session, error = self._new_code_session(old_cwd)
        if new_session is not None:
            self._code_sessions[session_id] = new_session
            self._code_sessions.move_to_end(session_id)
            return new_session.server, ""
        return None, error

    def _get_scratch_session(self, project_root: Path | None) -> tuple[_CodeSession | None, str]:
        key = str(project_root or "")
        existing = self._scratch_sessions.get(key)
        if existing is not None and existing.retirement_error:
            return None, existing.retirement_error
        if existing is not None:
            self._scratch_sessions.move_to_end(key)
            self._scratch_was_reused = True
            return existing, ""
        self._scratch_was_reused = False
        error = self._make_room(self._scratch_sessions, self.max_scratch_sessions)
        if error:
            return None, error
        session, error = self._new_code_session(project_root)
        if session is not None:
            self._scratch_sessions[key] = session
        return session, error

    def _drop_scratch_session(self, project_root: Path | None) -> None:
        key = str(project_root or "")
        stale = self._scratch_sessions.pop(key, None)
        if stale is not None:
            stale.close()

    _scratch_was_reused = False

    # ------------------------------------------------------------- incremental env

    def _ensure_header(
        self, session: _IncrementalSession, header: str, *, timeout_s: int
    ) -> tuple[bool, str, float, str, bool, bool]:
        """Returns ``(ok, error, elapsed, error_code, timed_out, cache_hit)``."""

        header_hash = _sha(header)
        if session.header_hash == header_hash and session.header_env is not None:
            return True, "", 0.0, "", False, True
        if session.header_hash and session.header_hash != header_hash:
            restarted, error = self._restart_session(session)
            if restarted is None:
                return False, error, 0.0, _error_code_for_message(error), _timeout_error_text(error), False
        response, elapsed, error, error_code, timed_out = run_command(
            session.server, header, env=None, include_tactics=False, timeout_s=timeout_s
        )
        if error:
            return False, error, elapsed, error_code, timed_out, False
        if response is None or bool(response.has_errors()):
            return False, "LeanInteract header warmup failed", elapsed, ErrorCode.HEADER_FAILED, False, False
        session.header_hash = header_hash
        session.header_env = getattr(response, "env", None)
        session.checkpoints.clear()
        return True, "", elapsed, "", False, False

    def _ensure_env_before(
        self,
        session: _IncrementalSession,
        segments: list[LeanIncrementalSegment],
        target_index: int,
        *,
        timeout_s: int,
    ) -> tuple[int | None, str, float, bool, str, bool]:
        env = session.header_env
        total_elapsed = 0.0
        cache_hit = True
        for segment in segments[:target_index]:
            checkpoint = session.checkpoints.get(segment.index)
            if (
                checkpoint is not None
                and checkpoint.before_env == env
                and checkpoint.text_hash == segment.text_hash
                and checkpoint.after_env is not None
            ):
                env = checkpoint.after_env
                continue
            cache_hit = False
            response, elapsed, error, error_code, timed_out = run_command(
                session.server, segment.text, env=env, include_tactics=False, timeout_s=timeout_s
            )
            total_elapsed += elapsed
            if error:
                return None, error, total_elapsed, cache_hit, error_code, timed_out
            if response is None or bool(response.has_errors()):
                messages = (
                    _message_payloads(response, line_offset=segment.start_line - 1, limit=4)
                    if response is not None
                    else []
                )
                summary = _format_message_summary(messages)
                detail = f": {summary}" if summary else ""
                return (
                    None,
                    f"failed to build env before target at {segment.name or segment.index}{detail}",
                    total_elapsed,
                    cache_hit,
                    ErrorCode.PRIOR_DECL_FAILED,
                    False,
                )
            after_env = getattr(response, "env", None)
            session.checkpoints[segment.index] = _Checkpoint(
                before_env=env, after_env=after_env, text_hash=segment.text_hash
            )
            env = after_env
        return env, "", total_elapsed, cache_hit, "", False

    # ------------------------------------------------------------- check pipeline

    def _check(
        self,
        *,
        action: str,
        file_path: str | Path,
        theorem_id: str,
        cwd: str | Path | None,
        replacement: str,
        include_tactics: bool,
        timeout_s: int,
        generation: str | None = None,
    ) -> dict[str, Any]:
        normalized_action = {"prepare_file": "prepare", "check_target": "check"}.get(action, action)
        project_root = self._resolve_project_root(cwd, file_path)
        if project_root is None:
            return error_envelope(
                action=normalized_action,
                error="Lean project root not detected",
                error_code=ErrorCode.NO_PROJECT_ROOT,
                searched=self._root_search_candidates(cwd, file_path),
            )
        resolved = self._resolve_file_path(file_path, project_root)
        if not resolved.is_file():
            return error_envelope(
                action=normalized_action,
                error="Lean file not found",
                error_code=ErrorCode.FILE_NOT_FOUND,
                file=resolved,
                project_root=project_root,
            )
        if replacement and normalized_action != "prepare" and not _looks_like_declaration(replacement):
            return error_envelope(
                action=normalized_action,
                error="replacement is not a complete declaration",
                error_code=ErrorCode.REPLACEMENT_NOT_A_DECLARATION,
                file=resolved,
                target=theorem_id,
                project_root=project_root,
            )
        text = resolved.read_text(encoding="utf-8")
        header, segments = segment_file(text)
        active_generation, generation_error = self._activate_generation(project_root, generation)
        if generation_error:
            return error_envelope(
                action=normalized_action,
                error=generation_error,
                error_code=ErrorCode.SESSION_CLEANUP_FAILED,
                file=resolved,
                project_root=project_root,
            )

        # Run the pipeline; on a dead REPL, restart once and rebuild from scratch
        # (fresh env ids — never replay a stale env against a restarted server).
        for attempt in (0, 1):
            session, error = self._get_session(project_root, resolved)
            if session is None:
                return error_envelope(
                    action=normalized_action,
                    error=error,
                    error_code=_error_code_for_message(error),
                    file=resolved,
                    project_root=project_root,
                )
            payload = self._run_check_pipeline(
                session=session,
                normalized_action=normalized_action,
                header=header,
                segments=segments,
                theorem_id=theorem_id,
                replacement=replacement,
                include_tactics=include_tactics,
                timeout_s=timeout_s,
                resolved=resolved,
                project_root=project_root,
            )
            if payload.get("error_code") == ErrorCode.DEAD_SERVER and attempt == 0:
                self._restart_session(session)
                continue
            payload.setdefault("cache", {})["generation"] = active_generation
            return payload
        return payload

    def _run_check_pipeline(
        self,
        *,
        session: _IncrementalSession,
        normalized_action: str,
        header: str,
        segments: list[LeanIncrementalSegment],
        theorem_id: str,
        replacement: str,
        include_tactics: bool,
        timeout_s: int,
        resolved: Path,
        project_root: Path,
    ) -> dict[str, Any]:
        ok_header, header_error, header_elapsed, header_error_code, header_timed_out, _hit = self._ensure_header(
            session, header, timeout_s=timeout_s
        )
        if not ok_header:
            return error_envelope(
                action=normalized_action,
                error=header_error,
                error_code=header_error_code or _error_code_for_message(header_error),
                file=resolved,
                project_root=project_root,
                timed_out=header_timed_out,
                elapsed_s=header_elapsed,
            )

        if normalized_action == "prepare":
            target = _find_segment(segments, theorem_id) if theorem_id else None
            if theorem_id and target is None:
                return self._target_not_found(normalized_action, resolved, project_root, segments, theorem_id)
            if target is not None:
                env, env_error, env_elapsed, cache_hit, env_error_code, env_timed_out = self._ensure_env_before(
                    session, segments, target.index, timeout_s=timeout_s
                )
                if env_error:
                    return error_envelope(
                        action=normalized_action,
                        error=env_error,
                        error_code=env_error_code,
                        file=resolved,
                        target=target.name,
                        project_root=project_root,
                        timed_out=env_timed_out,
                        elapsed_s=header_elapsed + env_elapsed,
                    )
                return {
                    "success": True,
                    "ok": True,
                    "backend": BACKEND,
                    "tool": TOOL,
                    "action": normalized_action,
                    "file": str(resolved),
                    "project_root": str(project_root),
                    "target": target.name,
                    "target_range": {"start_line": target.start_line, "end_line": target.end_line},
                    "elapsed_s": round(header_elapsed + env_elapsed, 3),
                    "timed_out": False,
                    "error_code": "",
                    "error": "",
                    "cache": {"header_env": session.header_env, "env_before": env, "cache_hit": cache_hit},
                }
            return {
                "success": True,
                "ok": True,
                "backend": BACKEND,
                "tool": TOOL,
                "action": normalized_action,
                "file": str(resolved),
                "project_root": str(project_root),
                "elapsed_s": round(header_elapsed, 3),
                "timed_out": False,
                "error_code": "",
                "error": "",
                "cache": {"header_env": session.header_env, "cache_hit": _hit},
            }

        target = _find_segment(segments, theorem_id)
        if target is None:
            return self._target_not_found(normalized_action, resolved, project_root, segments, theorem_id)
        env_before, env_error, env_elapsed, cache_hit, env_error_code, env_timed_out = self._ensure_env_before(
            session, segments, target.index, timeout_s=timeout_s
        )
        if env_error:
            return error_envelope(
                action=normalized_action,
                error=env_error,
                error_code=env_error_code or _error_code_for_message(env_error),
                file=resolved,
                target=target.name,
                project_root=project_root,
                timed_out=env_timed_out,
                elapsed_s=env_elapsed,
            )
        checked_text = str(replacement or "") or target.text
        want_tactics = include_tactics or normalized_action == "feedback"
        payload_include_tactics = want_tactics
        response, check_elapsed, check_error, check_error_code, check_timed_out = run_command(
            session.server, checked_text, env=env_before, include_tactics=want_tactics, timeout_s=timeout_s
        )
        if response is not None and not check_error and not want_tactics:
            has_errors, has_sorry, valid_without_sorry = outcome(response)
            if has_errors or not valid_without_sorry or has_sorry:
                tactic_response, tactic_elapsed, tactic_error, _tc, _tt = run_command(
                    session.server, checked_text, env=env_before, include_tactics=True, timeout_s=timeout_s
                )
                check_elapsed += tactic_elapsed
                if tactic_response is not None and not tactic_error:
                    response = tactic_response
                    payload_include_tactics = True
        env_after = getattr(response, "env", None) if response is not None else None
        if response is not None and not check_error and bool(outcome(response)[2]):
            session.checkpoints[target.index] = _Checkpoint(
                before_env=env_before, after_env=env_after, text_hash=_sha(checked_text)
            )
        return response_payload(
            response,
            action=normalized_action,
            file_path=resolved,
            target=target,
            elapsed_s=env_elapsed + check_elapsed,
            env_before=env_before,
            env_after=env_after,
            cache_hit=cache_hit,
            include_tactics=payload_include_tactics,
            checked_text=checked_text,
            project_root=project_root,
            timed_out=check_timed_out,
            error=check_error,
            error_code=check_error_code,
        )

    def _target_not_found(
        self,
        action: str,
        resolved: Path,
        project_root: Path,
        segments: list[LeanIncrementalSegment],
        theorem_id: str,
    ) -> dict[str, Any]:
        payload = error_envelope(
            action=action,
            error="target declaration not found",
            error_code=ErrorCode.TARGET_NOT_FOUND,
            file=resolved,
            target=theorem_id,
            project_root=project_root,
        )
        if hint := _mutual_target_hint(segments, theorem_id):
            payload["hint"] = hint
        return payload
