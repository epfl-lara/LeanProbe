"""LeanProbe core implementation.

The checker is intentionally scoped to the agent inner loop: same-file target
checks, replacement declaration screening, rich feedback, and tactic stepping.
Whole-file or whole-project release gates can still use Lake or CI.
"""

from __future__ import annotations

import hashlib
import platform
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DECLARATION_KINDS = (
    "theorem",
    "lemma",
    "example",
    "def",
    "instance",
    "class",
    "structure",
    "inductive",
    "abbrev",
    "axiom",
    "opaque",
)
DECLARATION_MODIFIERS = (
    "private",
    "protected",
    "noncomputable",
    "partial",
    "unsafe",
    "nonrec",
    "scoped",
    "local",
)
LEAN_IDENTIFIER_ATOM_PATTERN = r"(?:«[^»\n]+»|[^\W\d][\w']*)"
LEAN_IDENTIFIER_PATTERN = rf"{LEAN_IDENTIFIER_ATOM_PATTERN}(?:\.{LEAN_IDENTIFIER_ATOM_PATTERN})*"
LEAN_UNIVERSE_PATTERN = r"(?:\.\{[^}\n]*\})?"
LEAN_NAME_LOOKAHEAD = r"(?=[\s:({\[]|$)"
DECLARATION_PATTERN = re.compile(
    r"^[ \t]*"
    r"(?:(?:@\[[^\]]*\][ \t]*(?:\n[ \t]*)?)|"
    rf"(?:(?:{'|'.join(DECLARATION_MODIFIERS)})\b[ \t]+))*"
    rf"(?P<kind>{'|'.join(DECLARATION_KINDS)})\b"
    rf"(?:\s+(?P<name>{LEAN_IDENTIFIER_PATTERN}){LEAN_UNIVERSE_PATTERN}{LEAN_NAME_LOOKAHEAD})?",
    re.MULTILINE,
)
MUTUAL_PATTERN = re.compile(r"^[ \t]*mutual\b", re.MULTILINE)
MUTUAL_END_PATTERN = re.compile(r"^[ \t]*end\b", re.MULTILINE)
LOCAL_REPL_CANDIDATES = (
    ".lake/packages/repl",
    ".lake/build",
)
PROJECT_MARKERS = ("lakefile.lean", "lakefile.toml")
NOISY_MESSAGE_PREFIXES = ("note: this linter can be disabled with",)
DEFAULT_MESSAGE_LIMIT = 12
DEFAULT_TACTIC_LIMIT = 20
DEFAULT_SORRY_LIMIT = 20
FEEDBACK_TACTIC_LIMIT = 18
FEEDBACK_ENTRIES_PER_LINE = 4
FEEDBACK_MESSAGE_CHARS = 240
FEEDBACK_GOALS_CHARS = 300
DEFAULT_MAX_CODE_SESSIONS = 16


@dataclass(frozen=True)
class _SegmentStart:
    declaration_start: int
    kind: str
    name: str


@dataclass(frozen=True)
class LeanIncrementalSegment:
    """A top-level declaration chunk in a Lean file."""

    index: int
    kind: str
    name: str
    start: int
    end: int
    declaration_start: int
    start_line: int
    end_line: int
    text: str
    text_hash: str


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
    checkpoints: dict[int, _Checkpoint] = field(default_factory=dict)
    segment_names: dict[int, str] = field(default_factory=dict)

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

    def close(self) -> None:
        try:
            self.server.kill()
        except Exception:
            pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_lean_project_root(start: str | Path) -> Path | None:
    """Find the nearest Lake project root at or above ``start``."""

    path = Path(start).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if any((candidate / marker).is_file() for marker in PROJECT_MARKERS):
            return candidate
    return None


def _lean_code_mask(text: str) -> list[bool]:
    """Return a per-character mask for positions outside Lean comments/strings."""

    mask = [True] * len(text)
    i = 0
    block_depth = 0
    in_string = False
    in_line_comment = False
    while i < len(text):
        if in_line_comment:
            if text[i] == "\n":
                in_line_comment = False
            else:
                mask[i] = False
            i += 1
            continue
        if block_depth:
            mask[i] = False
            if text.startswith("/-", i):
                if i + 1 < len(mask):
                    mask[i + 1] = False
                block_depth += 1
                i += 2
                continue
            if text.startswith("-/", i):
                if i + 1 < len(mask):
                    mask[i + 1] = False
                block_depth -= 1
                i += 2
                continue
            i += 1
            continue
        if in_string:
            mask[i] = False
            if text[i] == "\\":
                if i + 1 < len(mask):
                    mask[i + 1] = False
                i += 2
                continue
            if text[i] == '"':
                in_string = False
            i += 1
            continue
        if text.startswith("--", i):
            mask[i] = False
            if i + 1 < len(mask):
                mask[i + 1] = False
            in_line_comment = True
            i += 2
            continue
        if text.startswith("/-", i):
            mask[i] = False
            if i + 1 < len(mask):
                mask[i + 1] = False
            block_depth = 1
            i += 2
            continue
        if text[i] == '"':
            mask[i] = False
            in_string = True
            i += 1
            continue
        i += 1
    return mask


def _line_indent_at(text: str, line_start: int) -> int:
    line_end = text.find("\n", line_start)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    return len(line) - len(line.lstrip(" \t"))


def _position_in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < pos < end for start, end in spans)


def _mutual_block_spans(text: str, code_mask: list[bool]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in MUTUAL_PATTERN.finditer(text):
        start = match.start()
        if not code_mask[start] or _position_in_spans(start, spans):
            continue
        opener_indent = _line_indent_at(text, start)
        end = len(text)
        first_next_line = text.find("\n", match.end())
        scan = first_next_line + 1 if first_next_line >= 0 else len(text)
        while scan < len(text):
            if code_mask[scan] and MUTUAL_END_PATTERN.match(text, scan):
                if _line_indent_at(text, scan) <= opener_indent:
                    line_end = text.find("\n", scan)
                    end = len(text) if line_end < 0 else line_end + 1
                    break
            next_line = text.find("\n", scan)
            if next_line < 0:
                break
            scan = next_line + 1
        spans.append((start, end))
    return spans


def _import_lean_interact() -> tuple[Any, Any, Any, Any, Any, str]:
    try:
        from lean_interact import Command, LeanREPLConfig, LeanServer, LocalProject, ProofStep
    except Exception as exc:
        return None, None, None, None, None, f"lean-interact unavailable: {exc}"
    return Command, ProofStep, LeanREPLConfig, LeanServer, LocalProject, ""


def _local_repl_dir(project_root: Path) -> Path | None:
    suffix = ".exe" if platform.system() == "Windows" else ""
    for candidate in LOCAL_REPL_CANDIDATES:
        root = project_root / candidate
        binary = root / ".lake" / "build" / "bin" / f"repl{suffix}"
        if binary.is_file():
            return root
    return None


def _doc_boundary_start(text: str, declaration_start: int) -> int:
    start = declaration_start
    while True:
        cursor = start
        while cursor > 0 and text[cursor - 1] in " \t\r\n":
            cursor -= 1
        line_start = text.rfind("\n", 0, cursor) + 1
        line = text[line_start:cursor].strip()
        if line.startswith("@[") and line.endswith("]"):
            start = line_start
            continue
        break

    cursor = start
    while cursor > 0 and text[cursor - 1] in " \t\r\n":
        cursor -= 1
    if cursor >= 2 and text[:cursor].endswith("-/"):
        start = text.rfind("/-", 0, cursor)
        if start >= 0 and text.startswith("/--", start) and text[cursor:declaration_start].strip() == "":
            return text.rfind("\n", 0, start) + 1
    return start


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def segment_file(text: str) -> tuple[str, list[LeanIncrementalSegment]]:
    """Split a Lean file into a header and top-level declaration chunks."""

    code_mask = _lean_code_mask(text)
    mutual_spans = _mutual_block_spans(text, code_mask)
    starts = [_SegmentStart(declaration_start=start, kind="mutual", name="") for start, _end in mutual_spans]
    starts.extend(
        _SegmentStart(
            declaration_start=match.start(),
            kind=str(match.group("kind") or ""),
            name=str(match.group("name") or "").strip(),
        )
        for match in DECLARATION_PATTERN.finditer(text)
        if code_mask[match.start()] and not _position_in_spans(match.start(), mutual_spans)
    )
    starts.sort(key=lambda item: item.declaration_start)
    if not starts:
        return text, []

    boundaries = [_doc_boundary_start(text, marker.declaration_start) for marker in starts]
    header = text[: boundaries[0]].rstrip() + "\n"
    segments: list[LeanIncrementalSegment] = []
    for index, marker in enumerate(starts):
        start = boundaries[index]
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(text)
        chunk = text[start:end].rstrip() + "\n"
        segments.append(
            LeanIncrementalSegment(
                index=index,
                kind=marker.kind,
                name=marker.name,
                start=start,
                end=end,
                declaration_start=marker.declaration_start,
                start_line=_line_number(text, start),
                end_line=_line_number(text, max(start, end - 1)),
                text=chunk,
                text_hash=_sha(chunk),
            )
        )
    return header, segments


def _find_segment(segments: list[LeanIncrementalSegment], theorem_id: str) -> LeanIncrementalSegment | None:
    wanted = str(theorem_id or "").strip()
    if not wanted:
        return None
    short = wanted.split(".")[-1]
    for segment in segments:
        if segment.name in {wanted, short}:
            return segment
    return None


def _pos_to_dict(pos: Any | None, *, line_offset: int = 0) -> dict[str, int] | None:
    if pos is None:
        return None
    line = int(getattr(pos, "line", 0) or 0)
    column = int(getattr(pos, "column", 0) or 0)
    return {"line": line + line_offset, "column": column}


def _clean_message_text(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        if any(line.strip().startswith(prefix) for prefix in NOISY_MESSAGE_PREFIXES):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _message_payloads(
    response: Any, *, line_offset: int = 0, limit: int = DEFAULT_MESSAGE_LIMIT
) -> list[dict[str, Any]]:
    payloads = []
    for message in list(getattr(response, "messages", []) or [])[:limit]:
        text = _clean_message_text(str(getattr(message, "data", "") or ""))
        payloads.append(
            {
                "severity": str(getattr(message, "severity", "") or ""),
                "message": text,
                "start": _pos_to_dict(getattr(message, "start_pos", None), line_offset=0),
                "end": _pos_to_dict(getattr(message, "end_pos", None), line_offset=0),
                "file_start": _pos_to_dict(getattr(message, "start_pos", None), line_offset=line_offset),
                "file_end": _pos_to_dict(getattr(message, "end_pos", None), line_offset=line_offset),
            }
        )
    return payloads


def _format_message_summary(messages: list[dict[str, Any]], *, limit: int = 3) -> str:
    parts: list[str] = []
    for item in messages[:limit]:
        pos = item.get("file_start") if isinstance(item.get("file_start"), Mapping) else item.get("start")
        location = ""
        if isinstance(pos, Mapping):
            line = pos.get("line")
            column = pos.get("column")
            if line:
                location = f"line {line}"
                if column is not None:
                    location += f":{column}"
        severity = str(item.get("severity", "") or "").strip()
        message = " ".join(str(item.get("message", "") or "").split())
        prefix = f"{location} " if location else ""
        if severity:
            prefix += f"{severity}: "
        if message:
            parts.append((prefix + message)[:240])
    return "; ".join(parts)


def _tactic_payloads(response: Any, *, line_offset: int = 0, limit: int = DEFAULT_TACTIC_LIMIT) -> list[dict[str, Any]]:
    payloads = []
    for tactic in list(getattr(response, "tactics", []) or [])[:limit]:
        payloads.append(
            {
                "tactic": str(getattr(tactic, "tactic", "") or ""),
                "goals": str(getattr(tactic, "goals", "") or ""),
                "proof_state": getattr(tactic, "proof_state", None),
                "start": _pos_to_dict(getattr(tactic, "start_pos", None), line_offset=0),
                "end": _pos_to_dict(getattr(tactic, "end_pos", None), line_offset=0),
                "file_start": _pos_to_dict(getattr(tactic, "start_pos", None), line_offset=line_offset),
                "file_end": _pos_to_dict(getattr(tactic, "end_pos", None), line_offset=line_offset),
                "used_constants": list(getattr(tactic, "used_constants", []) or []),
            }
        )
    return payloads


def _sorry_payloads(response: Any, *, limit: int = DEFAULT_SORRY_LIMIT) -> list[dict[str, Any]]:
    payloads = []
    for sorry in list(getattr(response, "sorries", []) or [])[:limit]:
        payloads.append(
            {
                "goal": str(getattr(sorry, "goal", "") or ""),
                "proof_state": getattr(sorry, "proof_state", None),
                "start": _pos_to_dict(getattr(sorry, "start_pos", None), line_offset=0),
                "end": _pos_to_dict(getattr(sorry, "end_pos", None), line_offset=0),
            }
        )
    return payloads


def _has_sorry(response: Any) -> bool:
    if list(getattr(response, "sorries", []) or []):
        return True
    for message in list(getattr(response, "messages", []) or []):
        if str(getattr(message, "data", "") or "") in {"declaration uses 'sorry'", "declaration uses `sorry`"}:
            return True
    return False


def _feedback_lean(
    text: str, messages: list[dict[str, Any]], tactics: list[dict[str, Any]], *, limit: int = FEEDBACK_TACTIC_LIMIT
) -> str:
    by_line: dict[int, list[str]] = {}
    for message in messages:
        pos = message.get("start") if isinstance(message.get("start"), Mapping) else None
        line = int(pos.get("line", 1) if isinstance(pos, Mapping) else 1)
        raw = str(message.get("message", "") or "").replace("\n", " ")
        entry = f"-- type: {message.get('severity', '')}, msg: {raw}"
        by_line.setdefault(max(1, line), []).append(entry[:FEEDBACK_MESSAGE_CHARS])
    for tactic in tactics[:limit]:
        pos = tactic.get("start") if isinstance(tactic.get("start"), Mapping) else None
        line = int(pos.get("line", 1) if isinstance(pos, Mapping) else 1)
        goals = str(tactic.get("goals", "") or "").strip()
        if goals:
            by_line.setdefault(max(1, line), []).append(
                "-- proof state: " + goals.replace("\n", " ")[:FEEDBACK_GOALS_CHARS]
            )

    output: list[str] = []
    for line_number, source_line in enumerate(text.splitlines(), start=1):
        if line_number in by_line:
            indent = source_line[: len(source_line) - len(source_line.lstrip())]
            output.append(f"{indent}/- <feedback>")
            output.extend(f"{indent}{entry}" for entry in by_line[line_number][:FEEDBACK_ENTRIES_PER_LINE])
            output.append(f"{indent}</feedback> -/")
        output.append(source_line)
    return "\n".join(output)


def _response_payload(
    response: Any,
    *,
    action: str,
    file_path: Path,
    target: LeanIncrementalSegment | None,
    elapsed_s: float,
    env_before: int | None,
    env_after: int | None,
    cache_hit: bool,
    include_tactics: bool,
    checked_text: str,
    timed_out: bool = False,
    error: str = "",
    error_code: str = "",
) -> dict[str, Any]:
    line_offset = (int(target.start_line) - 1) if target is not None else 0
    messages = _message_payloads(response, line_offset=line_offset) if response is not None else []
    tactics = _tactic_payloads(response, line_offset=line_offset) if response is not None and include_tactics else []
    has_errors = (
        bool(response.has_errors()) if response is not None and hasattr(response, "has_errors") else bool(error)
    )
    has_sorry = _has_sorry(response) if response is not None else False
    valid_without_sorry = (
        bool(response.lean_code_is_valid(allow_sorry=False))
        if response is not None and hasattr(response, "lean_code_is_valid")
        else False
    )
    output = "\n".join(
        f"{item.get('severity', '')}: {item.get('message', '')}".strip()
        for item in messages
        if str(item.get("message", "") or "").strip()
    )
    resolved_error_code = error_code or _error_code_for_message(error)
    return {
        "success": not bool(error),
        "ok": valid_without_sorry and not has_errors and not has_sorry,
        "backend": "lean_interact",
        "tool": "lean_probe",
        "action": action,
        "file": str(file_path),
        "target": target.name if target else "",
        "target_kind": target.kind if target else "",
        "target_range": {"start_line": target.start_line, "end_line": target.end_line} if target else {},
        "valid_without_sorry": valid_without_sorry,
        "has_errors": has_errors,
        "has_sorry": has_sorry,
        "timed_out": timed_out,
        "error_code": resolved_error_code if error else "",
        "error": error,
        "elapsed_s": round(float(elapsed_s), 3),
        "command": f"lean_probe {action}",
        "output": output or error,
        "messages": messages,
        "tactics": tactics,
        "feedback_lean": _feedback_lean(checked_text, messages, tactics) if (messages or tactics) else "",
        "cache": {
            "env_before": env_before,
            "env_after": env_after,
            "cache_hit": cache_hit,
            "header_env": env_before if target is None else None,
        },
    }


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
    if _timeout_error_text(lowered):
        return "timeout"
    if "failed to start leaninteract server" in lowered:
        return "lean_interact_start_failed"
    if "lean-interact unavailable" in lowered:
        return "lean_interact_unavailable"
    if _dead_server_error(lowered):
        return "dead_server"
    if "lean project root not detected" in lowered:
        return "no_project_root"
    if "lean file not found" in lowered:
        return "file_not_found"
    if "target declaration not found" in lowered:
        return "target_not_found"
    if "header warmup failed" in lowered:
        return "header_failed"
    if "failed to build env before target" in lowered:
        return "prior_decl_failed"
    return "backend_error"


def _error_code_for_exception(exc: BaseException) -> str:
    if _timeout_exception(exc):
        return "timeout"
    return _error_code_for_message(str(exc))


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
    ) -> None:
        self.auto_build = auto_build
        self.local_repl_path = Path(local_repl_path).expanduser().resolve() if local_repl_path else None
        self.lake_path = Path(lake_path)
        self.verbose = verbose
        self._sessions: dict[tuple[str, str], _IncrementalSession] = {}
        self._code_sessions: OrderedDict[str, _CodeSession] = OrderedDict()
        self.max_code_sessions = max(1, int(max_code_sessions))
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                session.close()
            self._sessions.clear()
            for code_session in list(self._code_sessions.values()):
                code_session.close()
            self._code_sessions.clear()

    def capabilities(self, cwd: str | Path | None = None) -> dict[str, Any]:
        with self._lock:
            _, _, _, _, _, import_error = _import_lean_interact()
            project_root = self._resolve_project_root(cwd)
            repl_dir = self._select_repl_dir(project_root) if project_root else None
            degraded: list[str] = []
            degraded_codes: list[str] = []
            if import_error:
                degraded.append(import_error)
                degraded_codes.append("lean_interact_unavailable")
            if project_root is None:
                degraded.append("Lean project root not detected")
                degraded_codes.append("no_project_root")
            return {
                "available": not import_error and bool(project_root),
                "project_root": str(project_root or ""),
                "repl_dir": str(repl_dir or ""),
                "active_sessions": [
                    {"project_root": project, "file": file_path} for project, file_path in self._sessions.keys()
                ],
                "code_sessions": list(self._code_sessions.keys()),
                "max_code_sessions": self.max_code_sessions,
                "degraded_reasons": degraded,
                "degraded_codes": degraded_codes,
            }

    def prepare_file(
        self,
        file_path: str | Path,
        *,
        theorem_id: str = "",
        cwd: str | Path | None = None,
        timeout_s: int = 60,
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
            )

    def check_target(
        self,
        file_path: str | Path,
        *,
        theorem_id: str,
        cwd: str | Path | None = None,
        replacement: str = "",
        include_tactics: bool = False,
        timeout_s: int = 60,
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
            )

    def feedback(
        self,
        file_path: str | Path,
        *,
        theorem_id: str,
        cwd: str | Path | None = None,
        replacement: str = "",
        timeout_s: int = 60,
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
            )

    def proof_state_from_code(
        self,
        code: str,
        *,
        cwd: str | Path | None = None,
        include_tactics: bool = False,
        timeout_s: int = 60,
    ) -> dict[str, Any]:
        with self._lock:
            session_id = str(uuid.uuid4())
            session, error = self._new_code_session(cwd)
            if session is None:
                return {
                    "success": False,
                    "ok": False,
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": "state",
                    "timed_out": False,
                    "error_code": _error_code_for_message(error),
                    "error": error,
                }
            self._remember_code_session(session_id, session)
            response, elapsed, run_error, run_error_code, timed_out = self._run_command(
                session.server,
                code,
                env=None,
                include_tactics=include_tactics,
                timeout_s=timeout_s,
                retry=lambda: self._restart_code_session(session_id),
            )
            messages = _message_payloads(response) if response is not None else []
            sorries = _sorry_payloads(response) if response is not None else []
            has_errors = (
                bool(response.has_errors())
                if response is not None and hasattr(response, "has_errors")
                else bool(run_error)
            )
            return {
                "success": not bool(run_error),
                "ok": not has_errors and bool(sorries),
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": "state",
                "session_id": session_id,
                "env": getattr(response, "env", None) if response is not None else None,
                "has_errors": has_errors,
                "timed_out": timed_out,
                "error_code": run_error_code if run_error else "",
                "error": run_error,
                "elapsed_s": round(elapsed, 3),
                "messages": messages,
                "sorries": sorries,
                "tactics": _tactic_payloads(response) if response is not None and include_tactics else [],
            }

    def close_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._code_sessions.pop(session_id, None)
            if session is None:
                return {
                    "success": False,
                    "ok": False,
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": "close_state",
                    "session_id": session_id,
                    "timed_out": False,
                    "error_code": "unknown_session",
                    "error": "unknown LeanProbe proof session",
                }
            session.close()
            return {
                "success": True,
                "ok": True,
                "backend": "lean_interact",
                "tool": "lean_probe",
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
                return {
                    "success": False,
                    "ok": False,
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": "step",
                    "session_id": session_id,
                    "timed_out": False,
                    "error_code": "unknown_session",
                    "error": "unknown LeanProbe proof session",
                }
            self._code_sessions.move_to_end(session_id)
            _, ProofStep, _, _, _, import_error = _import_lean_interact()
            if ProofStep is None:
                return {
                    "success": False,
                    "ok": False,
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": "step",
                    "session_id": session_id,
                    "timed_out": False,
                    "error_code": "lean_interact_unavailable",
                    "error": import_error,
                }
            start = time.perf_counter()
            try:
                response = session.server.run(ProofStep(proof_state=proof_state, tactic=tactic), timeout=timeout_s)
                elapsed = time.perf_counter() - start
                status = str(getattr(response, "proof_status", "") or "")
                return {
                    "success": True,
                    "ok": status == "Completed",
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": "step",
                    "session_id": session_id,
                    "proof_state": getattr(response, "proof_state", None),
                    "goals": list(getattr(response, "goals", []) or []),
                    "proof_status": status,
                    "elapsed_s": round(elapsed, 3),
                }
            except Exception as exc:
                error_code = _error_code_for_exception(exc)
                session_dead = error_code == "dead_server"
                if session_dead:
                    stale = self._code_sessions.pop(session_id, None)
                    if stale is not None:
                        stale.close()
                return {
                    "success": False,
                    "ok": False,
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": "step",
                    "session_id": session_id,
                    "timed_out": error_code == "timeout",
                    "error_code": "session_dead" if session_dead else error_code,
                    "session_dead": session_dead,
                    "hint": "call lean_probe_state again" if session_dead else "",
                    "error": str(exc),
                    "elapsed_s": round(time.perf_counter() - start, 3),
                }

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
            return _local_repl_dir(project_root)
        return None

    def _session_key(self, project_root: Path, file_path: Path) -> tuple[str, str]:
        return str(project_root.resolve()), str(file_path.resolve())

    def _new_session(
        self,
        project_root: Path,
        file_path: Path,
        repl_dir: Path | None,
    ) -> tuple[_IncrementalSession | None, str]:
        _, _, LeanREPLConfig, LeanServer, LocalProject, import_error = _import_lean_interact()
        if LeanREPLConfig is None or LeanServer is None or LocalProject is None:
            return None, import_error
        try:
            project = LocalProject(directory=str(project_root), auto_build=self.auto_build)
            kwargs: dict[str, Any] = {
                "project": project,
                "lake_path": str(self.lake_path),
                "verbose": self.verbose,
            }
            if repl_dir is not None:
                kwargs.update({"local_repl_path": str(repl_dir), "build_repl": False})
            config = LeanREPLConfig(**kwargs)
            server = LeanServer(config)
        except Exception as exc:
            return None, f"failed to start LeanInteract server: {exc}"
        return _IncrementalSession(
            project_root=project_root,
            file_path=file_path,
            repl_dir=repl_dir,
            server=server,
            config=config,
        ), ""

    def _get_session(self, project_root: Path, file_path: Path) -> tuple[_IncrementalSession | None, str]:
        repl_dir = self._select_repl_dir(project_root)
        key = self._session_key(project_root, file_path)
        existing = self._sessions.get(key)
        if existing and existing.repl_dir == repl_dir:
            return existing, ""
        if existing:
            existing.close()
        session, error = self._new_session(project_root, file_path, repl_dir)
        if session is not None:
            self._sessions[key] = session
        return session, error

    def _new_code_session(self, cwd: str | Path | None) -> tuple[_CodeSession | None, str]:
        _, _, LeanREPLConfig, LeanServer, LocalProject, import_error = _import_lean_interact()
        if LeanREPLConfig is None or LeanServer is None or LocalProject is None:
            return None, import_error
        project_root = self._resolve_project_root(cwd)
        if cwd and project_root is None:
            return None, "Lean project root not detected"
        try:
            kwargs: dict[str, Any] = {
                "lake_path": str(self.lake_path),
                "verbose": self.verbose,
            }
            if project_root is not None:
                kwargs["project"] = LocalProject(directory=str(project_root), auto_build=self.auto_build)
            repl_dir = self._select_repl_dir(project_root)
            if repl_dir is not None:
                kwargs.update({"local_repl_path": str(repl_dir), "build_repl": False})
            config = LeanREPLConfig(**kwargs)
            server = LeanServer(config)
        except Exception as exc:
            return None, f"failed to start LeanInteract server: {exc}"
        return _CodeSession(server=server, config=config, cwd=project_root), ""

    def _remember_code_session(self, session_id: str, session: _CodeSession) -> None:
        self._code_sessions[session_id] = session
        self._code_sessions.move_to_end(session_id)
        while len(self._code_sessions) > self.max_code_sessions:
            _old_id, old_session = self._code_sessions.popitem(last=False)
            old_session.close()

    def _restart_session(self, session: _IncrementalSession) -> tuple[_IncrementalSession | None, str]:
        key = self._session_key(session.project_root, session.file_path)
        session.close()
        self._sessions.pop(key, None)
        new_session, error = self._new_session(session.project_root, session.file_path, session.repl_dir)
        if new_session is not None:
            self._sessions[key] = new_session
        return new_session, error

    def _restart_incremental_server(self, session: _IncrementalSession) -> tuple[Any | None, str]:
        restarted, error = self._restart_session(session)
        if restarted is None:
            return None, error
        session.__dict__.update(restarted.__dict__)
        return session.server, ""

    def _restart_code_session(self, session_id: str) -> tuple[Any | None, str]:
        session = self._code_sessions.get(session_id)
        if session is None:
            return None, "unknown LeanProbe proof session"
        old_cwd = session.cwd
        session.close()
        self._code_sessions.pop(session_id, None)
        new_session, error = self._new_code_session(old_cwd)
        if new_session is not None:
            self._code_sessions[session_id] = new_session
            return new_session.server, ""
        return None, error

    def _run_command(
        self,
        server: Any,
        cmd: str,
        *,
        env: int | None,
        include_tactics: bool,
        timeout_s: int,
        retry: Any,
        retry_dead_server: bool = True,
    ) -> tuple[Any | None, float, str, str, bool]:
        Command, _, _, _, _, import_error = _import_lean_interact()
        if Command is None:
            return None, 0.0, import_error, "lean_interact_unavailable", False
        start = time.perf_counter()
        try:
            request = Command(cmd=cmd, all_tactics=True) if include_tactics else Command(cmd=cmd)
            if env is not None:
                request = request.model_copy(update={"env": env})
            response = server.run(request, timeout=timeout_s)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            error = str(exc)
            error_code = _error_code_for_exception(exc)
            timed_out = error_code == "timeout"
            if retry_dead_server and error_code == "dead_server":
                restarted_server, restart_error = retry()
                if restarted_server is None:
                    final_error = restart_error or error
                    return (
                        None,
                        elapsed,
                        final_error,
                        _error_code_for_message(final_error),
                        _timeout_error_text(final_error),
                    )
                response, retry_elapsed, retry_error, retry_error_code, retry_timed_out = self._run_command(
                    restarted_server,
                    cmd,
                    env=env,
                    include_tactics=include_tactics,
                    timeout_s=timeout_s,
                    retry=retry,
                    retry_dead_server=False,
                )
                return response, elapsed + retry_elapsed, retry_error, retry_error_code, retry_timed_out
            return None, time.perf_counter() - start, error, error_code, timed_out
        return response, time.perf_counter() - start, "", "", False

    def _ensure_header(
        self, session: _IncrementalSession, header: str, *, timeout_s: int
    ) -> tuple[bool, str, float, str, bool]:
        header_hash = _sha(header)
        if session.header_hash == header_hash and session.header_env is not None:
            return True, "", 0.0, "", False
        if session.header_hash and session.header_hash != header_hash:
            restarted, error = self._restart_session(session)
            if restarted is None:
                return False, error, 0.0, _error_code_for_message(error), _timeout_error_text(error)
            session.__dict__.update(restarted.__dict__)
        response, elapsed, error, error_code, timed_out = self._run_command(
            session.server,
            header,
            env=None,
            include_tactics=False,
            timeout_s=timeout_s,
            retry=lambda: self._restart_incremental_server(session),
        )
        if error:
            return False, error, elapsed, error_code, timed_out
        if response is None or bool(response.has_errors()):
            return False, "LeanInteract header warmup failed", elapsed, "header_failed", False
        session.header_hash = header_hash
        session.header_env = getattr(response, "env", None)
        session.checkpoints.clear()
        session.segment_names.clear()
        return True, "", elapsed, "", False

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
            response, elapsed, error, error_code, timed_out = self._run_command(
                session.server,
                segment.text,
                env=env,
                include_tactics=False,
                timeout_s=timeout_s,
                retry=lambda: self._restart_incremental_server(session),
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
                    "prior_decl_failed",
                    False,
                )
            after_env = getattr(response, "env", None)
            session.checkpoints[segment.index] = _Checkpoint(
                before_env=env, after_env=after_env, text_hash=segment.text_hash
            )
            session.segment_names[segment.index] = segment.name
            env = after_env
        return env, "", total_elapsed, cache_hit, "", False

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
    ) -> dict[str, Any]:
        normalized_action = {"prepare_file": "prepare", "check_target": "check"}.get(action, action)
        project_root = self._resolve_project_root(cwd, file_path)
        if project_root is None:
            return {
                "success": False,
                "ok": False,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "timed_out": False,
                "error_code": "no_project_root",
                "error": "Lean project root not detected",
            }
        resolved = self._resolve_file_path(file_path, project_root)
        if not resolved.is_file():
            return {
                "success": False,
                "ok": False,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "file": str(resolved),
                "timed_out": False,
                "error_code": "file_not_found",
                "error": "Lean file not found",
            }
        text = resolved.read_text(encoding="utf-8")
        header, segments = segment_file(text)
        session, error = self._get_session(project_root, resolved)
        if session is None:
            return {
                "success": False,
                "ok": False,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "file": str(resolved),
                "timed_out": False,
                "error_code": _error_code_for_message(error),
                "error": error,
            }
        ok_header, header_error, header_elapsed, header_error_code, header_timed_out = self._ensure_header(
            session,
            header,
            timeout_s=timeout_s,
        )
        if not ok_header:
            return {
                "success": False,
                "ok": False,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "file": str(resolved),
                "timed_out": header_timed_out,
                "error_code": header_error_code or _error_code_for_message(header_error),
                "error": header_error,
                "elapsed_s": round(header_elapsed, 3),
            }
        if normalized_action == "prepare":
            target = _find_segment(segments, theorem_id) if theorem_id else None
            if theorem_id and target is None:
                return {
                    "success": False,
                    "ok": False,
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": normalized_action,
                    "file": str(resolved),
                    "target": theorem_id,
                    "timed_out": False,
                    "error_code": "target_not_found",
                    "error": "target declaration not found",
                }
            if target is not None:
                env, env_error, env_elapsed, cache_hit, env_error_code, env_timed_out = self._ensure_env_before(
                    session,
                    segments,
                    target.index,
                    timeout_s=timeout_s,
                )
                return {
                    "success": not bool(env_error),
                    "ok": not bool(env_error),
                    "backend": "lean_interact",
                    "tool": "lean_probe",
                    "action": normalized_action,
                    "file": str(resolved),
                    "target": target.name,
                    "target_range": {"start_line": target.start_line, "end_line": target.end_line},
                    "elapsed_s": round(header_elapsed + env_elapsed, 3),
                    "timed_out": env_timed_out,
                    "error_code": env_error_code if env_error else "",
                    "error": env_error,
                    "cache": {"header_env": session.header_env, "env_before": env, "cache_hit": cache_hit},
                }
            return {
                "success": True,
                "ok": True,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "file": str(resolved),
                "elapsed_s": round(header_elapsed, 3),
                "cache": {"header_env": session.header_env, "cache_hit": header_elapsed == 0.0},
            }

        target = _find_segment(segments, theorem_id)
        if target is None:
            return {
                "success": False,
                "ok": False,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "file": str(resolved),
                "target": theorem_id,
                "timed_out": False,
                "error_code": "target_not_found",
                "error": "target declaration not found",
            }
        env_before, env_error, env_elapsed, cache_hit, env_error_code, env_timed_out = self._ensure_env_before(
            session,
            segments,
            target.index,
            timeout_s=timeout_s,
        )
        if env_error:
            return {
                "success": False,
                "ok": False,
                "backend": "lean_interact",
                "tool": "lean_probe",
                "action": normalized_action,
                "file": str(resolved),
                "target": target.name,
                "timed_out": env_timed_out,
                "error_code": env_error_code or _error_code_for_message(env_error),
                "error": env_error,
                "elapsed_s": round(env_elapsed, 3),
            }
        checked_text = str(replacement or "") or target.text
        want_tactics = include_tactics or normalized_action == "feedback"
        payload_include_tactics = want_tactics
        response, check_elapsed, check_error, check_error_code, check_timed_out = self._run_command(
            session.server,
            checked_text,
            env=env_before,
            include_tactics=want_tactics,
            timeout_s=timeout_s,
            retry=lambda: self._restart_incremental_server(session),
        )
        if response is not None and not check_error and not want_tactics:
            has_errors = bool(response.has_errors()) if hasattr(response, "has_errors") else False
            valid_without_sorry = (
                bool(response.lean_code_is_valid(allow_sorry=False))
                if hasattr(response, "lean_code_is_valid")
                else False
            )
            if has_errors or not valid_without_sorry or _has_sorry(response):
                tactic_response, tactic_elapsed, tactic_error, _tactic_error_code, _tactic_timed_out = (
                    self._run_command(
                        session.server,
                        checked_text,
                        env=env_before,
                        include_tactics=True,
                        timeout_s=timeout_s,
                        retry=lambda: self._restart_incremental_server(session),
                    )
                )
                check_elapsed += tactic_elapsed
                if tactic_response is not None and not tactic_error:
                    response = tactic_response
                    payload_include_tactics = True
        env_after = getattr(response, "env", None) if response is not None else None
        if response is not None and not check_error and bool(response.lean_code_is_valid(allow_sorry=False)):
            session.checkpoints[target.index] = _Checkpoint(
                before_env=env_before,
                after_env=env_after,
                text_hash=_sha(checked_text),
            )
            session.segment_names[target.index] = target.name
        return _response_payload(
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
            timed_out=check_timed_out,
            error=check_error,
            error_code=check_error_code,
        )
