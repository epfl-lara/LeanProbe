from __future__ import annotations

import json
import re
from importlib.metadata import version
from pathlib import Path

import anyio
import pytest

import lean_probe
from lean_probe import mcp_server
from lean_probe.mcp_server import (
    MCP_SERVER_NAME,
    SERVER_INSTRUCTIONS,
    TOOL_NAMES,
    _env_bool,
    _probe_from_env,
    _resolve_lake_path,
    create_server,
)


class _FakeProbe:
    def __init__(self):
        self.closed = False

    def capabilities(self, *args, **kwargs):
        return {"action": "status", "args": args, "kwargs": kwargs, "available": True}

    def check_code(self, *args, **kwargs):
        return {"action": "check", "args": args, "kwargs": kwargs, "ok": True}

    def check_target(self, *args, **kwargs):
        return {"action": "check_target", "args": args, "kwargs": kwargs}

    def feedback(self, *args, **kwargs):
        return {"action": "feedback", "args": args, "kwargs": kwargs}

    def proof_state_from_code(self, *args, **kwargs):
        return {"action": "state", "args": args, "kwargs": kwargs}

    def tactic_step(self, *args, **kwargs):
        return {"action": "step", "args": args, "kwargs": kwargs}

    def close_state(self, *args, **kwargs):
        return {"action": "close_state", "args": args, "kwargs": kwargs}

    def close(self):
        self.closed = True


def test_mcp_public_names_are_the_redesigned_set():
    assert MCP_SERVER_NAME == "lean-probe"
    assert TOOL_NAMES == [
        "lean_check",
        "lean_check_target",
        "lean_status",
        "lean_proof_state",
        "lean_tactic",
        "lean_close_proof",
    ]


def test_handshake_advertises_instructions_and_real_version():
    # This is exactly the regression that shipped: no instructions, wrong version.
    server = create_server(probe=_FakeProbe())
    options = server._mcp_server.create_initialization_options()
    assert options.instructions
    assert options.instructions == SERVER_INSTRUCTIONS
    assert "lean_check" in options.instructions
    assert "replacement" in options.instructions.lower()
    assert options.server_version == lean_probe.__version__
    assert options.server_version != version("mcp")  # not the mcp library version


def test_tool_descriptions_are_action_oriented():
    server = create_server(probe=_FakeProbe())
    tools = server._tool_manager._tools
    assert set(tools) == set(TOOL_NAMES)
    assert tools["lean_check"].description.lower().startswith("verify a standalone")
    assert "default" in tools["lean_check"].description.lower()
    assert "COMPLETE declaration" in tools["lean_check_target"].description
    assert "readiness" in tools["lean_status"].description.lower()


def test_tool_annotations_mark_read_only_and_destructive():
    server = create_server(probe=_FakeProbe())
    tools = server._tool_manager._tools
    assert tools["lean_check"].annotations.readOnlyHint is True
    assert tools["lean_check_target"].annotations.readOnlyHint is True
    assert tools["lean_status"].annotations.readOnlyHint is True
    assert tools["lean_close_proof"].annotations.destructiveHint is True


def test_tool_wrappers_route_to_injected_probe():
    server = create_server(probe=_FakeProbe())
    tools = server._tool_manager._tools

    check = tools["lean_check"].fn(code="theorem t : True := by trivial", cwd="/p", timeout_s=7)
    target = tools["lean_check_target"].fn(file="Demo.lean", name="demo", cwd="/p")
    fb = tools["lean_check_target"].fn(file="Demo.lean", name="demo", with_feedback=True)
    status = tools["lean_status"].fn(cwd="/p", warm=True)
    step = tools["lean_tactic"].fn(session_id="s", proof_state=3, tactic="rfl", timeout_s=5)
    close = tools["lean_close_proof"].fn(session_id="s")

    assert check["action"] == "check" and check["kwargs"]["cwd"] == "/p"
    assert target["action"] == "check_target" and target["kwargs"]["theorem_id"] == "demo"
    assert fb["action"] == "feedback"
    assert status["kwargs"] == {"warm": True, "generation": None}
    assert step == {"action": "step", "args": ("s", 3, "rfl"), "kwargs": {"timeout_s": 5}}
    assert close == {"action": "close_state", "args": ("s",), "kwargs": {}}


def test_in_memory_wire_round_trip():
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    server = create_server(probe=_FakeProbe())

    async def _run():
        async with connect(server) as client:
            init = await client.initialize()
            assert init.instructions == SERVER_INSTRUCTIONS
            assert init.serverInfo.version == lean_probe.__version__

            listing = await client.list_tools()
            assert [t.name for t in listing.tools] == TOOL_NAMES

            result = await client.call_tool("lean_check", {"code": "theorem t : True := by trivial"})
            assert result.isError is False
            data = json.loads(result.content[0].text)
            assert data["action"] == "check"
            assert data["ok"] is True

    anyio.run(_run)


def test_probe_reads_environment(monkeypatch):
    monkeypatch.setenv("LEAN_PROBE_AUTO_BUILD", "true")
    monkeypatch.setenv("LEAN_PROBE_LOCAL_REPL_PATH", "/tmp/repl")
    monkeypatch.setenv("LEAN_PROBE_LAKE_PATH", "/opt/lake")
    monkeypatch.setenv("LEAN_PROBE_VERBOSE", "1")
    monkeypatch.setenv("LEAN_PROBE_MAX_FILE_SESSIONS", "3")
    monkeypatch.setenv("LEAN_PROBE_MAX_SCRATCH_SESSIONS", "2")
    monkeypatch.setenv("LEAN_PROBE_MAX_CODE_SESSIONS", "7")
    probe = _probe_from_env()
    assert probe.auto_build is True
    assert probe.local_repl_path == Path("/tmp/repl").resolve()
    assert probe.lake_path == Path("/opt/lake")
    assert probe.verbose is True
    assert probe.max_file_sessions == 3
    assert probe.max_scratch_sessions == 2
    assert probe.max_code_sessions == 7


@pytest.mark.parametrize("value", ["", "invalid", "0", "-2"])
def test_probe_rejects_invalid_environment_pool_limit(monkeypatch, value):
    monkeypatch.setenv("LEAN_PROBE_MAX_FILE_SESSIONS", value)
    with pytest.raises(ValueError, match="LEAN_PROBE_MAX_FILE_SESSIONS must be a positive integer"):
        _probe_from_env()


def test_existing_tools_forward_explicit_generation():
    server = create_server(probe=_FakeProbe())
    tools = server._tool_manager._tools
    requests = [
        tools["lean_check"].fn(code="theorem t : True := by trivial", generation="built-v2"),
        tools["lean_check_target"].fn(file="Demo.lean", name="demo", generation="built-v2"),
        tools["lean_check_target"].fn(file="Demo.lean", name="demo", with_feedback=True, generation="built-v2"),
        tools["lean_status"].fn(generation="built-v2"),
        tools["lean_proof_state"].fn(code="theorem t : True := by sorry", generation="built-v2"),
    ]
    assert all(request["kwargs"]["generation"] == "built-v2" for request in requests)


def test_probe_defaults_are_stdio_safe(monkeypatch):
    for var in ("LEAN_PROBE_AUTO_BUILD", "LEAN_PROBE_VERBOSE", "LEAN_PROBE_LOCAL_REPL_PATH"):
        monkeypatch.delenv(var, raising=False)
    probe = _probe_from_env()
    assert probe.auto_build is False
    assert probe.verbose is False


def test_resolve_lake_path_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("LEAN_PROBE_LAKE_PATH", "/custom/lake")
    assert _resolve_lake_path() == "/custom/lake"


def test_env_bool_false_values(monkeypatch):
    for value in ["", "0", "false", "FALSE", "no", "off"]:
        monkeypatch.setenv("LEAN_PROBE_FLAG", value)
        assert _env_bool("LEAN_PROBE_FLAG") is False
    monkeypatch.delenv("LEAN_PROBE_FLAG")
    assert _env_bool("LEAN_PROBE_FLAG") is False


def test_shutdown_handler_registration_is_repeatable(monkeypatch):
    registered = []
    signals = []
    monkeypatch.setattr(mcp_server.atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(mcp_server.signal, "signal", lambda sig, handler: signals.append((sig, handler)))
    probe = _FakeProbe()
    mcp_server._install_shutdown_handlers(probe)
    mcp_server._install_shutdown_handlers(probe)
    assert registered == [probe.close, probe.close]
    assert len(signals) == 4


def test_skill_tool_table_matches_public_mcp_names():
    from lean_probe.skills import read_skill_text

    names = re.findall(r"\| `(lean_[a-z_]+)` \|", read_skill_text())
    assert names == TOOL_NAMES
