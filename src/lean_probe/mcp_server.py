"""MCP stdio server for LeanProbe."""

from __future__ import annotations

import atexit
import importlib
import os
import signal
from typing import Annotated, Any

from .core import LeanProbe

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
    "lean_probe_prepare",
    "lean_probe_check",
    "lean_probe_feedback",
    "lean_probe_state",
    "lean_probe_step",
    "lean_probe_close_state",
]

FilePath = Annotated[
    str,
    ParamField(description="Lean source file path, absolute or relative to cwd/project root."),
]
TheoremId = Annotated[
    str,
    ParamField(
        description="Target declaration name. Qualified and unqualified names are accepted when they match the file."
    ),
]
Cwd = Annotated[
    str,
    ParamField(
        description="Lean/Lake project root, or a directory inside it. Leave empty to auto-detect from file_path/current working directory."
    ),
]
Replacement = Annotated[
    str,
    ParamField(
        description="Complete replacement declaration chunk: signature plus proof/body. Leave empty to check the current target text."
    ),
]
TimeoutS = Annotated[
    int,
    ParamField(description="LeanInteract request timeout in seconds."),
]
IncludeTactics = Annotated[
    bool,
    ParamField(description="When true, collect tactic ranges, goals, proof states, and used constants."),
]
LeanCode = Annotated[
    str,
    ParamField(
        description="Standalone Lean code containing one or more sorry terms from which proof states should be created."
    ),
]
SessionId = Annotated[
    str,
    ParamField(description="LeanProbe proof session id returned by lean_probe_state."),
]
ProofStateId = Annotated[
    int,
    ParamField(description="Proof-state id returned by lean_probe_state or a previous lean_probe_step call."),
]
TacticText = Annotated[
    str,
    ParamField(description="One Lean tactic to apply to the given proof state, for example rfl, omega, or exact h."),
]


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _probe_from_env() -> LeanProbe:
    return LeanProbe(
        auto_build=_env_bool("LEAN_PROBE_AUTO_BUILD"),
        local_repl_path=os.environ.get("LEAN_PROBE_LOCAL_REPL_PATH") or None,
        lake_path=os.environ.get("LEAN_PROBE_LAKE_PATH") or "lake",
        verbose=_env_bool("LEAN_PROBE_VERBOSE"),
    )


def create_server(probe: LeanProbe | None = None) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:
        raise RuntimeError(f"mcp package unavailable: {exc}. Install with `pip install 'lean-probe[mcp]'`.") from exc

    active_probe = probe or _probe_from_env()
    mcp = FastMCP(MCP_SERVER_NAME)

    @mcp.tool()
    def lean_probe_prepare(
        file_path: FilePath,
        theorem_id: TheoremId = "",
        cwd: Cwd = "",
        timeout_s: TimeoutS = 60,
    ) -> dict[str, Any]:
        """Warm a Lean file header/imports and optionally prior declarations.

        Use before repeated checks in the same file or before moving to a later
        target. The file must resolve inside a Lean/Lake project. Environments
        are cached only inside this running MCP server process.
        """

        return active_probe.prepare_file(
            file_path,
            theorem_id=theorem_id,
            cwd=cwd or None,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def lean_probe_check(
        file_path: FilePath,
        theorem_id: TheoremId,
        cwd: Cwd = "",
        replacement: Replacement = "",
        include_tactics: IncludeTactics = False,
        timeout_s: TimeoutS = 60,
    ) -> dict[str, Any]:
        """Check one target declaration or complete replacement declaration.

        `replacement` must be the complete declaration chunk, including the
        signature and proof/body; it is not only a proof body. `success=false`
        reports tool/project failure. `success=true, ok=false` is a Lean result.
        """

        return active_probe.check_target(
            file_path,
            theorem_id=theorem_id,
            cwd=cwd or None,
            replacement=replacement,
            include_tactics=include_tactics,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def lean_probe_feedback(
        file_path: FilePath,
        theorem_id: TheoremId,
        cwd: Cwd = "",
        replacement: Replacement = "",
        timeout_s: TimeoutS = 60,
    ) -> dict[str, Any]:
        """Check with richer diagnostics, tactic metadata, and feedback_lean.

        Use after lean_probe_check when messages are not enough, or when an
        agent needs proof states and annotated Lean context. `replacement` has
        the same complete-declaration rule as lean_probe_check. This is usually
        costlier than check because it requests tactic metadata.
        """

        return active_probe.feedback(
            file_path,
            theorem_id=theorem_id,
            cwd=cwd or None,
            replacement=replacement,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def lean_probe_state(
        code: LeanCode,
        cwd: Cwd = "",
        include_tactics: IncludeTactics = False,
        timeout_s: TimeoutS = 60,
    ) -> dict[str, Any]:
        """Create proof states from standalone Lean code containing sorry.

        Returns a session_id and one proof_state id per sorry when Lean accepts
        the code with sorry. `ok=true` means proof states were extracted, not
        that the proof is complete. `ok=false` with success=true usually means
        Lean accepted the code but no sorry proof states were present.
        """

        return active_probe.proof_state_from_code(
            code,
            cwd=cwd or None,
            include_tactics=include_tactics,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def lean_probe_step(
        session_id: SessionId,
        proof_state: ProofStateId,
        tactic: TacticText,
        timeout_s: TimeoutS = 60,
    ) -> dict[str, Any]:
        """Apply one tactic to a LeanProbe proof state.

        Use a session_id from lean_probe_state and a proof_state id from state
        or a previous step. `ok=true` means LeanInteract reports the proof as
        Completed; otherwise inspect returned goals/proof_state. Session and
        proof_state ids die with the MCP server process.
        """

        return active_probe.tactic_step(
            session_id,
            proof_state,
            tactic,
            timeout_s=timeout_s,
        )

    @mcp.tool()
    def lean_probe_close_state(
        session_id: SessionId,
    ) -> dict[str, Any]:
        """Close a proof-state session created by lean_probe_state.

        Use when tactic exploration for a state is finished. Long-lived MCP
        servers also evict old state sessions automatically, but explicit close
        releases the LeanInteract process immediately.
        """

        return active_probe.close_state(session_id)

    return mcp


def _install_shutdown_handlers(probe: LeanProbe) -> None:
    atexit.register(probe.close)

    def _handler(signum: int, _frame: Any) -> None:
        probe.close()
        raise SystemExit(128 + signum)

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _handler)


def run() -> None:
    probe = _probe_from_env()
    _install_shutdown_handlers(probe)
    try:
        create_server(probe=probe).run()
    finally:
        probe.close()


if __name__ == "__main__":
    run()
