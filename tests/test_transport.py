"""Check linear response framing and instance-local backend selection."""

from __future__ import annotations

import io
import threading
from typing import Any

import pytest

from lean_probe import sessions, transport


class _Input:
    """Record a query and its flush without invoking a REPL process."""

    def __init__(self) -> None:
        self.value = ""
        self.flushed = False

    def write(self, value: str) -> None:
        self.value += value

    def flush(self) -> None:
        self.flushed = True


class _Process:
    """Expose the stream surface consumed by a LeanInteract transport."""

    def __init__(self, output: str = '{"env": 17}\n\n') -> None:
        self.stdin = _Input()
        self.stdout: Any = io.StringIO(output)


class _Server:
    """Supply inherited server fields so a selected subclass can execute normally."""

    def __init__(self, config: Any = None, output: str = '{"env": 17}\n\n') -> None:
        self.config = config
        self._proc = _Process(output)
        self._lock = threading.Lock()
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        release = getattr(self._proc.stdout, "release", None)
        if release is not None:
            release.set()


class _VulnerableServer(_Server):
    def _execute_cmd_in_repl(self, json_query: str, verbose: bool, timeout: float | None) -> str:
        output = ""
        for line in []:
            output += line
        return output


class _FixedServer(_Server):
    def _execute_cmd_in_repl(self, json_query: str, verbose: bool, timeout: float | None) -> str:
        """The old reader used output += line; this reader is already fixed."""
        return "fixed"


class _UnfamiliarServer(_Server):
    def _execute_cmd_in_repl(self, query: str) -> str:
        output = ""
        for line in []:
            output += line
        return output


def test_read_response_large_payload_leaves_following_response_unread() -> None:
    lines = [f'{{"entry": {index}}}\n' for index in range(100_000)]
    body = "".join(lines) + "\n"
    stream = io.StringIO(body + "next-response\n\n")
    assert transport.read_repl_response(stream) == body
    assert stream.read() == "next-response\n\n"


@pytest.mark.parametrize(
    ("body", "following"),
    [
        ('{"env": 1}\n\n', "next\n\n"),
        ('\n{"env": 1}\n\n', "next\n\n"),
        ('{"env": 1}', ""),
        ('{"env": 1}\r\n\r\n', ""),
    ],
)
def test_read_response_preserves_legacy_suffix_and_eof_behavior(body: str, following: str) -> None:
    stream = io.StringIO(body + following)
    assert transport.read_repl_response(stream) == body
    assert stream.read() == following


def test_linear_query_preserves_wire_protocol_and_stdout_capture() -> None:
    server = _Server()
    result = transport._linear_execute_cmd_in_repl(server, '{"cmd": "example : True := by trivial"}', False, 1.0)
    assert result == '{"env": 17}\n\n'
    assert server._proc.stdin.value == '{"cmd": "example : True := by trivial"}\n\n'
    assert server._proc.stdin.flushed and not server.killed


@pytest.mark.parametrize("output", ["", "\n", "\n\n", " \n\n"])
def test_empty_response_preserves_broken_pipe_error(output: str) -> None:
    with pytest.raises(BrokenPipeError, match="returned no output"):
        transport._linear_execute_cmd_in_repl(_Server(output=output), "{}", False, 1.0)


def test_reader_stream_failure_is_reported_without_uncaught_thread_error() -> None:
    class FailedStream:
        def readline(self) -> str:
            raise OSError("closed response stream")

    server = _Server()
    server._proc.stdout = FailedStream()
    with pytest.raises(BrokenPipeError, match="response stream closed unexpectedly") as failure:
        transport._linear_execute_cmd_in_repl(server, "{}", False, 1.0)
    assert isinstance(failure.value.__cause__, OSError)


def test_timeout_kills_worker_and_preserves_timeout_error() -> None:
    class BlockingStream:
        def __init__(self) -> None:
            self.release = threading.Event()

        def readline(self) -> str:
            self.release.wait()
            return ""

    server = _Server()
    server._proc.stdout = BlockingStream()
    with pytest.raises(TimeoutError, match="did not respond in time.*is now killed"):
        transport._linear_execute_cmd_in_repl(server, "{}", False, 0.001)
    assert server.killed and server._proc.stdout.release.is_set()


def test_timeout_reader_shutdown_wait_is_bounded(monkeypatch) -> None:
    joins = []

    class StuckThread:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["daemon"] is True

        def start(self) -> None:
            pass

        def join(self, timeout: float | None) -> None:
            joins.append(timeout)

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(transport.threading, "Thread", StuckThread)
    with pytest.raises(TimeoutError):
        transport._linear_execute_cmd_in_repl(_Server(), "{}", False, 0.01)
    assert joins == [0.01, transport.READER_SHUTDOWN_WAIT_S]


def test_known_vulnerable_reader_uses_private_subclass_without_mutation() -> None:
    original = _VulnerableServer._execute_cmd_in_repl
    selected = transport.server_class_for_transport(_VulnerableServer)
    assert selected is not _VulnerableServer and issubclass(selected, _VulnerableServer)
    assert selected._execute_cmd_in_repl is transport._linear_execute_cmd_in_repl
    assert _VulnerableServer._execute_cmd_in_repl is original
    assert transport.server_class_for_transport(_VulnerableServer) is selected
    assert selected()._execute_cmd_in_repl("{}", False, 1.0) == '{"env": 17}\n\n'


@pytest.mark.parametrize("server_class", [_FixedServer, _UnfamiliarServer, _Server])
def test_fixed_and_unfamiliar_backends_are_retained(server_class: type[Any]) -> None:
    assert transport.server_class_for_transport(server_class) is server_class


def test_uninspectable_backend_is_retained(monkeypatch) -> None:
    class Uninspectable(_VulnerableServer):
        pass

    def missing_source(function: Any) -> str:
        raise OSError("backend source unavailable")

    monkeypatch.setattr(transport.inspect, "getsource", missing_source)
    assert transport.server_class_for_transport(Uninspectable) is Uninspectable


def test_backend_method_update_invalidates_selection_cache(monkeypatch) -> None:
    class Updating(_VulnerableServer):
        pass

    selected = transport.server_class_for_transport(Updating)
    assert selected is not Updating
    monkeypatch.setattr(Updating, "_execute_cmd_in_repl", _FixedServer._execute_cmd_in_repl)
    assert transport.server_class_for_transport(Updating) is Updating


def test_build_server_routes_actual_import_path_through_selected_backend(monkeypatch, tmp_path) -> None:
    import lean_interact

    class Config:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class Project:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    original = _VulnerableServer._execute_cmd_in_repl
    monkeypatch.setattr(lean_interact, "LeanServer", _VulnerableServer)
    monkeypatch.setattr(lean_interact, "LeanREPLConfig", Config)
    monkeypatch.setattr(lean_interact, "LocalProject", Project)
    server, config, error = sessions.build_server(
        project_root=tmp_path, repl_dir=None, lake_path="lake", auto_build=False, verbose=False
    )
    assert not error and server.config is config
    assert isinstance(server, _VulnerableServer) and type(server) is not _VulnerableServer
    assert server._execute_cmd_in_repl("{}", False, 1.0) == '{"env": 17}\n\n'
    assert _VulnerableServer._execute_cmd_in_repl is original


def test_installed_backend_import_preserves_class_and_selects_locally() -> None:
    from lean_interact import LeanServer

    original = LeanServer._execute_cmd_in_repl
    selected = sessions._import_lean_interact()[3]
    assert selected is transport.server_class_for_transport(LeanServer)
    assert LeanServer._execute_cmd_in_repl is original
    assert issubclass(selected, LeanServer)
    if selected is not LeanServer:
        assert selected._execute_cmd_in_repl is transport._linear_execute_cmd_in_repl
