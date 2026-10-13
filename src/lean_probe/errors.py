"""Stable machine-readable error codes and actionable agent hints.

``error_code`` is the routing key agents should branch on (never the free-text
``error``). ``hint_for_code`` turns a code into a one-line "do this next"
instruction so a cold agent can recover without reading docs.
"""

from __future__ import annotations


class ErrorCode:
    """Catalog of every ``error_code`` LeanProbe can emit."""

    TIMEOUT = "timeout"
    LEAN_INTERACT_START_FAILED = "lean_interact_start_failed"
    LEAN_INTERACT_UNAVAILABLE = "lean_interact_unavailable"
    DEAD_SERVER = "dead_server"
    SESSION_DEAD = "session_dead"
    SESSION_CLEANUP_FAILED = "session_cleanup_failed"
    UNKNOWN_SESSION = "unknown_session"
    NO_PROJECT_ROOT = "no_project_root"
    FILE_NOT_FOUND = "file_not_found"
    TARGET_NOT_FOUND = "target_not_found"
    REPLACEMENT_NOT_A_DECLARATION = "replacement_not_a_declaration"
    HEADER_FAILED = "header_failed"
    PRIOR_DECL_FAILED = "prior_decl_failed"
    BACKEND_ERROR = "backend_error"


# Every code the contract documents, for tests/instructions to stay in sync.
ALL_ERROR_CODES = (
    ErrorCode.NO_PROJECT_ROOT,
    ErrorCode.FILE_NOT_FOUND,
    ErrorCode.TARGET_NOT_FOUND,
    ErrorCode.REPLACEMENT_NOT_A_DECLARATION,
    ErrorCode.LEAN_INTERACT_UNAVAILABLE,
    ErrorCode.LEAN_INTERACT_START_FAILED,
    ErrorCode.HEADER_FAILED,
    ErrorCode.PRIOR_DECL_FAILED,
    ErrorCode.DEAD_SERVER,
    ErrorCode.SESSION_DEAD,
    ErrorCode.SESSION_CLEANUP_FAILED,
    ErrorCode.UNKNOWN_SESSION,
    ErrorCode.TIMEOUT,
    ErrorCode.BACKEND_ERROR,
)


def _dead_server_error(error: str) -> bool:
    lowered = error.lower()
    return any(
        token in lowered
        for token in (
            "lean server is not running",
            "server is not running",
            "broken pipe",
            "connection reset",
            "process has exited",
        )
    )


def _timeout_error_text(error: str) -> bool:
    lowered = str(error or "").lower()
    return "timeout" in lowered or "timed out" in lowered


def _timeout_exception(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or _timeout_error_text(type(exc).__name__) or _timeout_error_text(str(exc))


def _error_code_for_message(error: str) -> str:
    lowered = str(error or "").lower()
    if not lowered:
        return ""
    if "session cleanup failed" in lowered:
        return ErrorCode.SESSION_CLEANUP_FAILED
    if _timeout_error_text(lowered):
        return ErrorCode.TIMEOUT
    if "failed to start leaninteract server" in lowered:
        return ErrorCode.LEAN_INTERACT_START_FAILED
    if "lean-interact unavailable" in lowered:
        return ErrorCode.LEAN_INTERACT_UNAVAILABLE
    if _dead_server_error(lowered):
        return ErrorCode.DEAD_SERVER
    if "lean project root not detected" in lowered:
        return ErrorCode.NO_PROJECT_ROOT
    if "lean file not found" in lowered:
        return ErrorCode.FILE_NOT_FOUND
    if "target declaration not found" in lowered:
        return ErrorCode.TARGET_NOT_FOUND
    if "header warmup failed" in lowered:
        return ErrorCode.HEADER_FAILED
    if "failed to build env before target" in lowered:
        return ErrorCode.PRIOR_DECL_FAILED
    return ErrorCode.BACKEND_ERROR


def _error_code_for_exception(exc: BaseException) -> str:
    if _timeout_exception(exc):
        return ErrorCode.TIMEOUT
    return _error_code_for_message(str(exc))


def hint_for_code(code: str, *, searched: list[str] | None = None) -> str:
    """Return a one-line, actionable recovery hint for an error code."""

    if code == ErrorCode.NO_PROJECT_ROOT:
        base = (
            "Set cwd to the absolute directory that contains the Lake project's "
            "lakefile.lean/lakefile.toml, then retry."
        )
        if searched:
            base += " Searched: " + ", ".join(searched) + "."
        return base
    if code == ErrorCode.FILE_NOT_FOUND:
        return "Pass an absolute file path, or a path relative to the project root (cwd)."
    if code == ErrorCode.TARGET_NOT_FOUND:
        return (
            "Use the exact declaration name as written in the file (qualified or unqualified). "
            "Call lean_status or re-read the file to confirm the name."
        )
    if code == ErrorCode.REPLACEMENT_NOT_A_DECLARATION:
        return (
            "replacement must be a COMPLETE declaration (full signature AND body), e.g. "
            "'theorem foo : P := by ...', not just a proof body. To check a bare snippet use lean_check."
        )
    if code == ErrorCode.LEAN_INTERACT_UNAVAILABLE:
        return "Install the backend: pip install 'lean-interact'."
    if code == ErrorCode.LEAN_INTERACT_START_FAILED:
        return (
            "The Lean REPL failed to start. Ensure the project is built (lake build) and that lake is on PATH "
            "or LEAN_PROBE_LAKE_PATH is set."
        )
    if code in (ErrorCode.DEAD_SERVER, ErrorCode.SESSION_DEAD):
        return "The Lean REPL process died. Retry; for proof states call lean_proof_state again."
    if code == ErrorCode.UNKNOWN_SESSION:
        return "That session id is unknown (the server restarted). Call lean_proof_state to create a new one."
    if code == ErrorCode.SESSION_CLEANUP_FAILED:
        return (
            "A worker could not be verified stopped; ownership and the previous generation remain retained. "
            "Inspect the worker process, arrange its termination, and retry cleanup before starting replacement work."
        )
    if code == ErrorCode.TIMEOUT:
        return (
            "The Lean call timed out. The environment is likely warm now, so a retry should be faster; "
            "or raise timeout_s."
        )
    if code == ErrorCode.HEADER_FAILED:
        return "The file's imports/header failed to elaborate. Confirm the project provides those imports."
    if code == ErrorCode.PRIOR_DECL_FAILED:
        return "A declaration before the target failed to elaborate; fix earlier declarations first."
    if code == ErrorCode.BACKEND_ERROR:
        return "The Lean backend reported an unexpected error; inspect `error` for details."
    return ""
