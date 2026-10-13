"""Select an instance-local linear reader for known quadratic LeanInteract backends."""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap
import threading
from functools import lru_cache
from typing import IO, Any, cast

logger = logging.getLogger(__name__)
READER_SHUTDOWN_WAIT_S = 2.0


def read_repl_response(stdout: IO[str]) -> str:
    """Collect one response in linear time and preserve the backend's exact delimiter."""

    chunks: list[str] = []
    suffix = ""
    while True:
        line = stdout.readline()
        if not line:
            break
        chunks.append(line)
        # Retain only the last two characters rather than copying the whole body
        # at every line. This also preserves leading blank lines and short reads.
        suffix = (suffix + line)[-2:] if len(line) < 2 else line[-2:]
        if suffix == "\n\n":
            break
    return "".join(chunks)


def _linear_execute_cmd_in_repl(
    self: Any,
    json_query: str,
    verbose: bool,
    timeout: float | None,
) -> str:
    """Send a backend query and gather its response without quadratic concatenation."""

    process = self._proc
    assert process is not None and process.stdin is not None and process.stdout is not None
    with self._lock:
        if verbose:
            logger.info("Sending query: %s", json_query)
        process.stdin.write(json_query + "\n\n")
        process.stdin.flush()
        responses: list[str] = []
        reader_errors: list[Exception] = []

        def reader() -> None:
            try:
                responses.append(read_repl_response(process.stdout))
            except Exception as exc:
                reader_errors.append(exc)

        thread = threading.Thread(target=reader, daemon=True, name="lean-probe-repl-reader")
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            self.kill()
            # Closing stdout normally releases readline; avoid keeping shutdown
            # unbounded if a backend delays releasing its pipe.
            thread.join(READER_SHUTDOWN_WAIT_S)
            raise TimeoutError(f"The Lean server did not respond in time ({timeout=}) and is now killed.")
        if reader_errors:
            raise BrokenPipeError("The Lean server response stream closed unexpectedly.") from reader_errors[0]
        output = responses[0] if responses else ""
        if output.strip():
            return output
        raise BrokenPipeError("The Lean server returned no output.")


def server_class_for_transport(server_class: type[Any]) -> type[Any]:
    """Choose a private subclass only for the recognized vulnerable backend reader.

    Never modify LeanInteract's class globally. Key the cache by both class and
    reader so a replaced/fixed backend method is inspected again.
    """

    current = getattr(server_class, "_execute_cmd_in_repl", None)
    if not inspect.isfunction(current):
        return server_class
    try:
        hash(server_class)
    except TypeError:
        return server_class
    return _select_server_class(cast(Any, server_class), current)


@lru_cache(maxsize=16)
def _select_server_class(server_class: type[Any], current: Any) -> type[Any]:
    """Inspect a backend reader once and retain unfamiliar or already fixed versions."""

    try:
        source = inspect.getsource(current)
        if "output += line" not in source:
            return server_class
        if list(inspect.signature(current).parameters) != ["self", "json_query", "verbose", "timeout"]:
            return server_class
        tree = ast.parse(textwrap.dedent(source))
    except (OSError, TypeError, ValueError, SyntaxError):
        return server_class
    if not any(
        isinstance(node, ast.AugAssign)
        and isinstance(node.op, ast.Add)
        and isinstance(node.target, ast.Name)
        and node.target.id == "output"
        and isinstance(node.value, ast.Name)
        and node.value.id == "line"
        for node in ast.walk(tree)
    ):
        return server_class
    return type(
        f"Linear{server_class.__name__}",
        (server_class,),
        {"__module__": __name__, "_execute_cmd_in_repl": _linear_execute_cmd_in_repl},
    )
