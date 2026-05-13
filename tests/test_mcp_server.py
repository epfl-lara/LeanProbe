import re
from pathlib import Path

from lean_probe import mcp_server
from lean_probe.mcp_server import MCP_SERVER_NAME, TOOL_NAMES, _env_bool, _probe_from_env, create_server


class _FakeProbe:
    def __init__(self):
        self.closed = False

    def capabilities(self, *args, **kwargs):
        return {"action": "capabilities", "args": args, "kwargs": kwargs}

    def prepare_file(self, *args, **kwargs):
        return {"action": "prepare", "args": args, "kwargs": kwargs}

    def check_target(self, *args, **kwargs):
        return {"action": "check", "args": args, "kwargs": kwargs}

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


def test_mcp_public_names_are_stable():
    assert MCP_SERVER_NAME == "lean-probe"
    assert TOOL_NAMES == [
        "lean_probe_capabilities",
        "lean_probe_prepare",
        "lean_probe_check",
        "lean_probe_feedback",
        "lean_probe_state",
        "lean_probe_step",
        "lean_probe_close_state",
    ]


def test_mcp_server_constructs():
    server = create_server()
    assert server is not None


def test_mcp_tool_descriptions_expose_agent_contracts():
    server = create_server(probe=_FakeProbe())
    tools = server._tool_manager._tools

    assert "readiness" in tools["lean_probe_capabilities"].description
    assert "degraded_codes" in tools["lean_probe_capabilities"].description
    assert "cached only inside this running MCP server process" in tools["lean_probe_prepare"].description
    assert "complete declaration chunk" in tools["lean_probe_check"].description
    assert "success=false" in tools["lean_probe_check"].description
    feedback_description = " ".join(tools["lean_probe_feedback"].description.split())
    assert "usually costlier than check" in feedback_description
    assert "same complete-declaration rule" in feedback_description
    assert "Create proof states" in tools["lean_probe_state"].description
    step_description = " ".join(tools["lean_probe_step"].description.split())
    assert "Session and proof_state ids die" in step_description
    assert "Close a proof-state session" in tools["lean_probe_close_state"].description

    check_params = tools["lean_probe_check"].parameters["properties"]
    assert "signature plus proof/body" in check_params["replacement"]["description"]
    assert "Lean/Lake project root" in check_params["cwd"]["description"]


def test_mcp_tool_wrappers_call_injected_probe():
    server = create_server(probe=_FakeProbe())
    tools = server._tool_manager._tools

    capabilities = tools["lean_probe_capabilities"].fn(cwd="/tmp/project")
    check = tools["lean_probe_check"].fn(
        file_path="Demo.lean",
        theorem_id="demo",
        cwd="/tmp/project",
        replacement="theorem demo : True := by\n  trivial\n",
        include_tactics=True,
        timeout_s=7,
    )
    step = tools["lean_probe_step"].fn(session_id="session", proof_state=3, tactic="rfl", timeout_s=5)
    close = tools["lean_probe_close_state"].fn(session_id="session")

    assert capabilities["action"] == "capabilities"
    assert capabilities["kwargs"]["cwd"] == "/tmp/project"
    assert check["action"] == "check"
    assert check["kwargs"]["cwd"] == "/tmp/project"
    assert check["kwargs"]["include_tactics"] is True
    assert step == {"action": "step", "args": ("session", 3, "rfl"), "kwargs": {"timeout_s": 5}}
    assert close == {"action": "close_state", "args": ("session",), "kwargs": {}}


def test_mcp_probe_reads_environment(monkeypatch):
    monkeypatch.setenv("LEAN_PROBE_AUTO_BUILD", "true")
    monkeypatch.setenv("LEAN_PROBE_LOCAL_REPL_PATH", "/tmp/repl")
    monkeypatch.setenv("LEAN_PROBE_LAKE_PATH", "/opt/lake")
    monkeypatch.setenv("LEAN_PROBE_VERBOSE", "1")

    probe = _probe_from_env()

    assert probe.auto_build is True
    assert probe.local_repl_path == Path("/tmp/repl").resolve()
    assert probe.lake_path == Path("/opt/lake")
    assert probe.verbose is True


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


def test_agent_tool_table_matches_public_mcp_names():
    agent_md = (Path(__file__).resolve().parents[1] / "AGENT.md").read_text(encoding="utf-8")
    names = re.findall(r"\| `(lean_probe_[a-z_]+)` \|", agent_md)

    assert names == TOOL_NAMES
