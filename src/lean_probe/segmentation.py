"""Lean file segmentation: split a file into a header and top-level declarations.

A "segment" is one top-level declaration chunk (theorem/def/instance/...), or a
whole ``mutual ... end`` block treated as a single context chunk. Segmentation is
indentation-aware: a declaration keyword only starts a new top-level segment when
it sits at column 0, so ``where``/``let rec``/nested helper declarations stay
attached to their parent instead of being torn off into bogus chunks.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left
from dataclasses import dataclass

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


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    if cursor >= 2 and text.endswith("-/", 0, cursor):
        start = text.rfind("/-", 0, cursor)
        if start >= 0 and text.startswith("/--", start) and text[cursor:declaration_start].strip() == "":
            return text.rfind("\n", 0, start) + 1
    return start


def _line_number(text: str, offset: int, newline_offsets: list[int] | None = None) -> int:
    if newline_offsets is not None:
        return bisect_left(newline_offsets, max(0, offset)) + 1
    return text.count("\n", 0, max(0, offset)) + 1


def _segment_file_chunks(text: str, *, source_text: str | None = None) -> tuple[str, list[LeanIncrementalSegment]]:
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
        if code_mask[match.start()]
        and not _position_in_spans(match.start(), mutual_spans)
        and _line_indent_at(text, match.start()) == 0
    )
    starts.sort(key=lambda item: item.declaration_start)
    if not starts:
        return text, []

    source = text if source_text is None else source_text
    newline_offsets = [match.start() for match in re.finditer("\n", source)]
    boundaries = [_doc_boundary_start(source, marker.declaration_start) for marker in starts]
    header = source[: boundaries[0]].rstrip() + "\n"
    segments: list[LeanIncrementalSegment] = []
    for index, marker in enumerate(starts):
        start = boundaries[index]
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(source)
        chunk = source[start:end].rstrip() + "\n"
        segments.append(
            LeanIncrementalSegment(
                index=index,
                kind=marker.kind,
                name=marker.name,
                start=start,
                end=end,
                declaration_start=marker.declaration_start,
                start_line=_line_number(source, start, newline_offsets),
                end_line=_line_number(source, max(start, end - 1), newline_offsets),
                text=chunk,
                text_hash=_sha(chunk),
            )
        )
    return header, segments


_SCOPED_COMMAND_PREFIX_RE = r"(?:set_option|variable|include|omit|attribute|open(?:[ \t]+scoped)?)"
_SCOPED_COMMAND_WRAPPER_BEFORE_DECL_RE = re.compile(
    rf"(?m)(^[ \t]*(?:{_SCOPED_COMMAND_PREFIX_RE}[^\n]*\bin" r"(?:[ \t]*\n[ \t]*|[ \t]+))+)\Z"
)
_INLINE_SCOPED_DECLARATION_RE = re.compile(
    rf"(?m)^(?P<wrapper>[ \t]*{_SCOPED_COMMAND_PREFIX_RE}\b[^\n]*\bin)"
    r"(?P<space>[ \t]+)"
    r"(?P<declaration>(?:(?:private|protected|noncomputable|unsafe|partial|nonrec|scoped|local)\s+)*"
    r"(?:theorem|lemma|example|def|abbrev|opaque|axiom|instance|class|structure|inductive)\b)"
)


def _normalize_inline_scoped_declarations(text: str) -> str:
    """Expose same-line scoped declarations to LeanProbe without shifting offsets."""

    def replace(match: re.Match[str]) -> str:
        spacing = match.group("space")
        return match.group("wrapper") + "\n" + (" " * (len(spacing) - 1)) + match.group("declaration")

    return _INLINE_SCOPED_DECLARATION_RE.sub(replace, text)


def _rebuilt_segment(
    segment: LeanIncrementalSegment,
    text: str,
    *,
    start: int,
    end: int,
    newline_offsets: list[int] | None = None,
) -> LeanIncrementalSegment:
    """Return one segment rebuilt over an exact source range."""
    segment_text = text[start:end]
    return LeanIncrementalSegment(
        index=int(segment.index),
        kind=str(segment.kind),
        name=str(segment.name),
        start=start,
        end=end,
        declaration_start=int(getattr(segment, "declaration_start", start) or start),
        start_line=_line_number(text, start, newline_offsets),
        end_line=max(_line_number(text, end, newline_offsets) - 1, _line_number(text, start, newline_offsets)),
        text=segment_text,
        text_hash=hashlib.sha256(segment_text.encode("utf-8")).hexdigest(),
    )


def _repair_option_wrapped_segments(
    text: str,
    header: str,
    segments: list[LeanIncrementalSegment],
) -> tuple[str, list[LeanIncrementalSegment]]:
    """Attach a preceding scoped command such as ``variable ... in`` to its declaration.

    LeanProbe's generic segmenter otherwise places the wrapper in the header
    or previous declaration. Incremental replay then elaborates a dangling
    ``in`` and reports an unexpected EOF against the wrong prerequisite.
    """
    if not segments:
        return header, segments
    repaired = list(segments)
    repaired_header = header
    newline_offsets = [match.start() for match in re.finditer("\n", text)]
    for index, segment in enumerate(tuple(repaired)):
        start = int(getattr(segment, "start", 0) or 0)
        # A wrapper belongs to the gap following the previous declaration.
        # Searching every full prefix makes large files quadratic; retain the
        # previous repaired start so same-line wrappers remain in the window.
        search_start = int(getattr(repaired[index - 1], "start", 0) or 0) if index else 0
        match = _SCOPED_COMMAND_WRAPPER_BEFORE_DECL_RE.search(text, search_start, start)
        if match is None:
            continue
        wrapper_start = match.start(1)
        if index == 0:
            repaired_header = text[:wrapper_start]
        else:
            previous = repaired[index - 1]
            previous_start = int(getattr(previous, "start", 0) or 0)
            repaired[index - 1] = _rebuilt_segment(
                previous,
                text,
                start=previous_start,
                end=wrapper_start,
                newline_offsets=newline_offsets,
            )
        repaired[index] = _rebuilt_segment(
            segment,
            text,
            start=wrapper_start,
            end=int(getattr(segment, "end", start) or start),
            newline_offsets=newline_offsets,
        )
    return repaired_header, repaired


def segment_file(text: str) -> tuple[str, list[LeanIncrementalSegment]]:
    """Split source while preserving scoped commands and exact declaration ranges."""
    normalized = _normalize_inline_scoped_declarations(text)
    header, segments = _segment_file_chunks(normalized, source_text=text)
    return _repair_option_wrapped_segments(text, header, segments)


def _find_segment(segments: list[LeanIncrementalSegment], theorem_id: str) -> LeanIncrementalSegment | None:
    wanted = str(theorem_id or "").strip()
    if not wanted:
        return None
    short = wanted.split(".")[-1]
    for segment in segments:
        if segment.name in {wanted, short}:
            return segment
    return None


def _mutual_target_hint(segments: list[LeanIncrementalSegment], theorem_id: str) -> str:
    wanted = str(theorem_id or "").strip()
    if not wanted:
        return ""
    names = {wanted, wanted.split(".")[-1]}
    for segment in segments:
        if segment.kind != "mutual":
            continue
        code_mask = _lean_code_mask(segment.text)
        for match in DECLARATION_PATTERN.finditer(segment.text):
            if not code_mask[match.start()]:
                continue
            name = str(match.group("name") or "").strip()
            if name in names:
                return (
                    "target appears inside a mutual block; LeanProbe uses mutual blocks as context chunks "
                    "and does not target their inner declarations individually"
                )
    return ""
