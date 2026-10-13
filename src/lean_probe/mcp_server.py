"""MCP stdio server for LeanProbe.

The server advertises agent-facing ``instructions`` on connect, reports the real
LeanProbe version in ``serverInfo``, and exposes a small, action-oriented tool
set. ``lean_check`` is the low-friction default (verify any snippet);
``lean_check_target`` is the fast warm-environment path for project files.
"""

from __future__ import annotations

import atexit
import importlib
import logging
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Annotated, Any

from . import __version__
from .errors import ALL_ERROR_CODES
from .probe import (
    DEFAULT_MAX_CODE_SESSIONS,
    DEFAULT_MAX_FILE_SESSIONS,
    DEFAULT_MAX_SCRATCH_SESSIONS,
    LeanProbe,
)

_pydantic: Any
try:
    _pydantic = importlib.import_module("pydantic")
except Exception:
    _pydantic = None


def ParamField(*, description: str) -> Any:
    if _pydantic is None:
        return None
    return _pydantic.Field(description=description)


MCP_SERVER_NAME = "lean-probe"
TOOL_NAMES = [
    "lean_check",
    "lean_check_target",
    "lean_status",
    "lean_proof_state",
    "lean_tactic",
    "lean_close_proof",
]

SERVER_INSTRUCTIONS = (
    "LeanProbe: fast Lean 4 proof feedback over a warm REPL. Use it in the inner loop to\n"
    "verify Lean code far faster than `lake build`. Still run `lake build` as the final\n"
    "whole-project gate before committing; LeanProbe never edits files.\n"
    "\n"
    "START HERE\n"
    "- Verify ANY Lean code: call `lean_check` with `code` (include the imports the snippet\n"
    "  needs). No file path or declaration name required. This is the default tool.\n"
    "- Check or replace a declaration inside a project file: `lean_check_target` with `file`\n"
    "  and `name` (and optional `replacement`); it reuses the file's warm prior environment,\n"
    "  so repeated checks of the same file are very fast.\n"
    "- Explore a goal tactic by tactic: `lean_proof_state` -> `lean_tactic` -> `lean_close_proof`.\n"
    "- Check readiness or warm the project up front: `lean_status` (pass warm=true to pay\n"
    "  cold-start now).\n"
    "- Workers are bounded per session family; lean_status reports the configured limits.\n"
    "- generation on check/target/status/state is an optional caller-owned dependency/build\n"
    "  identity. Change it after preparing changed imports, toolchains, or build options to\n"
    "  discard that project's warm workers. LeanProbe never checks dependency freshness or\n"
    "  builds imports automatically by default. Omitting generation retains the active token.\n"
    "- Scoped declaration parsing and response transport are native; no client parser patches\n"
    "  are needed for inline or stacked command wrappers.\n"
    "- session_cleanup_failed means worker exit could not be verified. Ownership and the\n"
    "  previous generation remain retained; arrange termination and retry cleanup.\n"
    "\n"
    "PROJECT ROOT (cwd) is OPTIONAL: LeanProbe auto-detects the nearest Lake project\n"
    "(lakefile.lean/lakefile.toml) from the file, then from the server's working directory.\n"
    'If detection fails you get success=false, error_code="no_project_root", and a `hint`\n'
    "naming what to pass — set cwd to the absolute directory holding the lakefile and retry.\n"
    "An explicit cwd MUST be inside a Lake project. `import Mathlib` resolves only if that\n"
    "project depends on Mathlib.\n"
    "\n"
    "READING RESULTS (two levels):\n"
    "- success = did the tool run. false = an environment problem (no project root, file not\n"
    "  found, timeout, REPL crash); read error_code + hint and fix that first.\n"
    "- ok = did Lean accept the code. success=true with ok=false is a real Lean result —\n"
    "  inspect `messages`. ok=true means it elaborated with NO errors and NO sorry; warnings\n"
    "  alone do not flip ok. Scope is the submitted code plus its environment, not the whole\n"
    "  project, so `lake build` remains the final gate.\n"
    "\n"
    "REPLACEMENT must be a COMPLETE declaration (full signature AND body), e.g.\n"
    "'theorem foo : P := by ...', never a bare proof body. A bare body is rejected with\n"
    'error_code="replacement_not_a_declaration". When in doubt, use lean_check on the\n'
    "whole snippet.\n"
    "\n"
    "LATENCY: the first call after startup pays cold-start (REPL boot + imports; tens of\n"
    "seconds for Mathlib). Allow a generous client timeout on the first call, or call\n"
    "lean_status(warm=true) once. Proof-state/session ids live only in this process; recreate\n"
    "them after a restart. error_code routing values: " + ", ".join(ALL_ERROR_CODES) + "."
)

File = Annotated[
    str,
    ParamField(description="Lean source file path, absolute or relative to cwd/project root."),
]
Name = Annotated[
    str,
    ParamField(
        description="Target declaration name (theorem/def/instance/...). Qualified or unqualified, as written in the file."
    ),
]
Cwd = Annotated[
    str,
    ParamField(
        description="Lake project root, or any directory inside it. Leave empty to auto-detect from file/working dir."
    ),
]
Replacement = Annotated[
    str,
    ParamField(
        description="A COMPLETE replacement declaration: full signature plus proof/body (not just a proof body). "
        "Leave empty to check the declaration already in the file."
    ),
]
WithFeedback = Annotated[
    bool,
    ParamField(description="When true, include tactic proof states and an annotated `feedback_lean` block (slower)."),
]
TimeoutS = Annotated[
    int,
    ParamField(description="LeanInteract request timeout in seconds (the first Mathlib call may need most of it)."),
]
IncludeTactics = Annotated[
    bool,
    ParamField(description="When true, also collect tactic ranges, goals, proof states, and used constants."),
]
LeanCode = Annotated[
    str,
    ParamField(description="Standalone Lean code, including any imports/opens it needs."),
]
LeanCodeWithSorry = Annotated[
    str,
    ParamField(description="Standalone Lean code containing one or more `sorry` terms to open as proof states."),
]
SessionId = Annotated[
    str,
    ParamField(description="Proof session id returned by lean_proof_state."),
]
ProofStateId = Annotated[
    int,
    ParamField(description="Proof-state id from lean_proof_state or a previous lean_tactic call."),
]
TacticText = Annotated[
    str,
    ParamField(description="One Lean tactic to apply, e.g. rfl, omega, simp, or exact h."),
]
Warm = Annotated[
    bool,
    ParamField(description="When true, boot the Lean REPL now so the first real check is fast."),
]
Generation = Annotated[
    str,
    ParamField(
        description="Optional caller-owned prepared dependency/build identity. A changed nonempty token discards "
        "this project's warm workers, including proof states. Build changed imports first; LeanProbe "
        "does not calculate this token or verify dependency freshness. Empty retains the current identity."
    ),
]


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _env_session_limit(name: str, default: int) -> int:
    """Parse an optional positive worker limit and reject misleading configuration."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _resolve_lake_path() -> str:
    explicit = os.environ.get("LEAN_PROBE_LAKE_PATH")
    if explicit:
        return explicit
    found = shutil.which("lake")
    if found:
        return found
    elan_lake = Path.home() / ".elan" / "bin" / "lake"
    if elan_lake.is_file():
        return str(elan_lake)
    return "lake"


def _probe_from_env() -> LeanProbe:
    # Defaults are stdio-safe: auto_build and verbose stay OFF so build/REPL chatter
    # never lands on stdout (which carries JSON-RPC frames).
    return LeanProbe(
        auto_build=_env_bool("LEAN_PROBE_AUTO_BUILD"),
        local_repl_path=os.environ.get("LEAN_PROBE_LOCAL_REPL_PATH") or None,
        lake_path=_resolve_lake_path(),
        verbose=_env_bool("LEAN_PROBE_VERBOSE"),
        max_code_sessions=_env_session_limit("LEAN_PROBE_MAX_CODE_SESSIONS", DEFAULT_MAX_CODE_SESSIONS),
        max_file_sessions=_env_session_limit("LEAN_PROBE_MAX_FILE_SESSIONS", DEFAULT_MAX_FILE_SESSIONS),
        max_scratch_sessions=_env_session_limit("LEAN_PROBE_MAX_SCRATCH_SESSIONS", DEFAULT_MAX_SCRATCH_SESSIONS),
    )


def create_server(probe: LeanProbe | None = None) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except Exception as exc:
        raise RuntimeError(f"mcp package unavailable: {exc}. Install with `pip install 'lean-probe[mcp]'`.") from exc

    active_probe = probe or _probe_from_env()
    mcp = FastMCP(MCP_SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    # FastMCP has no `version` kwarg; the low-level server's version is what the
    # client reads in serverInfo during the handshake.
    mcp._mcp_server.version = __version__

    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    read_only_idempotent = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

    @mcp.tool(annotations=read_only_idempotent)
    def lean_check(
        code: LeanCode,
        cwd: Cwd = "",
        include_tactics: IncludeTactics = False,
        timeout_s: TimeoutS = 90,
        generation: Generation = "",
    ) -> dict[str, Any]:
        """Verify a standalone Lean 4 snippet and return diagnostics; the default checker.

        Pass the full snippet (imports + declarations). `ok=true` means it elaborated with
        no errors and no `sorry`. No file path or declaration name is needed. For repeated
        checks of one project file, prefer lean_check_target (it reuses a warm environment).
        """

        return active_probe.check_code(
            code, cwd=cwd or None, include_tactics=include_tactics, timeout_s=timeout_s, generation=generation or None
        )

    @mcp.tool(annotations=read_only_idempotent)
    def lean_check_target(
        file: File,
        name: Name,
        replacement: Replacement = "",
        cwd: Cwd = "",
        with_feedback: WithFeedback = False,
        include_tactics: IncludeTactics = False,
        timeout_s: TimeoutS = 90,
        generation: Generation = "",
    ) -> dict[str, Any]:
        """Check one named declaration in a project file against its warm prior environment.

        Use `replacement` (a COMPLETE declaration) to test a candidate without editing the
        file. Set `with_feedback=true` for proof states and an annotated `feedback_lean`
        block when a plain failure message is not enough. `success=false` is a tool/project
        problem; `success=true, ok=false` is a Lean rejection — inspect `messages`.
        """

        if with_feedback:
            return active_probe.feedback(
                file,
                theorem_id=name,
                cwd=cwd or None,
                replacement=replacement,
                timeout_s=timeout_s,
                generation=generation or None,
            )
        return active_probe.check_target(
            file,
            theorem_id=name,
            cwd=cwd or None,
            replacement=replacement,
            include_tactics=include_tactics,
            timeout_s=timeout_s,
            generation=generation or None,
        )

    @mcp.tool(annotations=read_only_idempotent)
    def lean_status(
        cwd: Cwd = "",
        warm: Warm = False,
        generation: Generation = "",
    ) -> dict[str, Any]:
        """Report readiness: project root, REPL path, live sessions, and any degraded reasons.

        Call first when setup is uncertain. `available=false` means setup is incomplete (see
        `degraded_codes`/`hint`), not a Lean result. Pass `warm=true` to boot the REPL now so
        the first real check does not pay cold-start.
        """

        return active_probe.capabilities(cwd or None, warm=warm, generation=generation or None)

    @mcp.tool(annotations=read_only)
    def lean_proof_state(
        code: LeanCodeWithSorry,
        cwd: Cwd = "",
        include_tactics: IncludeTactics = False,
        timeout_s: TimeoutS = 90,
        generation: Generation = "",
    ) -> dict[str, Any]:
        """Open interactive proof states from code containing `sorry`.

        Returns a `session_id` and one proof-state id per `sorry`. `ok=true` means at least
        one proof state was extracted (not that the proof is complete). Drive each state with
        lean_tactic, then lean_close_proof when done. Sessions live only in this process.
        """

        return active_probe.proof_state_from_code(
            code, cwd=cwd or None, include_tactics=include_tactics, timeout_s=timeout_s, generation=generation or None
        )

    @mcp.tool(annotations=read_only)
    def lean_tactic(
        session_id: SessionId,
        proof_state: ProofStateId,
        tactic: TacticText,
        timeout_s: TimeoutS = 60,
    ) -> dict[str, Any]:
        """Apply one tactic to a proof state from lean_proof_state.

        `ok=true` means the proof is Completed; otherwise use the returned `proof_state` and
        `goals` for the next tactic. If `error_code="session_dead"`, call lean_proof_state
        again to recreate the state.
        """

        return active_probe.tactic_step(session_id, proof_state, tactic, timeout_s=timeout_s)

    @mcp.tool(annotations=destructive)
    def lean_close_proof(
        session_id: SessionId,
    ) -> dict[str, Any]:
        """Release a proof-state session created by lean_proof_state.

        Call when tactic exploration for that session is finished to free its REPL process.
        """

        return active_probe.close_state(session_id)

    return mcp


def _install_shutdown_handlers(probe: LeanProbe) -> None:
    atexit.register(probe.close)

    def _handler(signum: int, frame: Any, previous: Any = None) -> None:
        probe.close()
        if callable(previous) and previous is not signal.default_int_handler:
            previous(signum, frame)
        raise SystemExit(128 + signum)

    def _chained_handler(previous: Any) -> Any:
        def _inner(signum: int, frame: Any) -> None:
            _handler(signum, frame, previous)

        return _inner

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            previous = signal.getsignal(sig)
            signal.signal(sig, _chained_handler(previous))


def _configure_stdio_logging() -> None:
    """Keep backend diagnostics off the JSON-RPC stream without changing the Python API."""
    try:
        importlib.import_module("lean_interact")
    except Exception:
        # Missing backend dependencies remain structured tool errors. Configure
        # its logger even if a partial import already installed a stdout handler.
        pass
    logger = logging.getLogger("lean_interact")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    logger.handlers = [handler]
    logger.propagate = False


def run() -> None:
    _configure_stdio_logging()
    probe = _probe_from_env()
    _install_shutdown_handlers(probe)
    try:
        create_server(probe=probe).run()
    finally:
        probe.close()


if __name__ == "__main__":
    run()
