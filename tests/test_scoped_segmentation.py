"""Verify native scoped-command parsing and bounded repair work."""

import pytest

from lean_probe import segmentation
from lean_probe.probe import _looks_like_declaration


@pytest.mark.parametrize(
    "source",
    [
        "open scoped Classical in theorem target : True := by trivial",
        "set_option maxRecDepth 10000 in private theorem target : True := by trivial",
        "variable (P : Type) in abbrev Target := P",
    ],
)
def test_inline_scoped_replacements_are_complete_declarations(source):
    assert _looks_like_declaration(source)


def test_segment_file_attaches_set_option_wrapper_to_private_theorem():
    header, segments = segmentation.segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem first : True := by",
                "  trivial",
                "",
                "set_option maxRecDepth 10000 in",
                "private theorem wrapped : True := by",
                "  trivial",
                "",
                "theorem last : True := by",
                "  trivial",
                "",
            ]
        )
    )

    assert header == "import Mathlib\n"
    assert [segment.name for segment in segments] == ["first", "wrapped", "last"]
    assert "set_option" not in segments[0].text
    assert segments[1].text.startswith("set_option maxRecDepth 10000 in")


def test_segment_file_recognizes_same_line_set_option_theorem():
    source = "\n".join(
        [
            "import Mathlib",
            "",
            "set_option maxHeartbeats 1000000 in theorem result : True := by",
            "  trivial",
            "",
        ]
    )

    header, segments = segmentation.segment_file(source)

    assert header == "import Mathlib\n\n"
    assert [segment.name for segment in segments] == ["result"]
    assert segments[0].text.startswith("set_option maxHeartbeats 1000000 in theorem result")
    assert source[segments[0].start : segments[0].end] == segments[0].text
    assert segments[0].start_line == 3


def test_segment_file_attaches_stacked_option_wrappers_to_private_theorem():
    header, segments = segmentation.segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem first : True := by",
                "  trivial",
                "",
                "set_option maxHeartbeats 50000000 in",
                "set_option maxRecDepth 100000 in",
                "private theorem wrapped : True := by",
                "  trivial",
                "",
                "theorem last : True := by",
                "  trivial",
                "",
            ]
        )
    )

    assert header == "import Mathlib\n"
    assert [segment.name for segment in segments] == ["first", "wrapped", "last"]
    assert "set_option" not in segments[0].text
    assert segments[1].text.startswith("set_option maxHeartbeats 50000000 in")
    assert "set_option maxRecDepth 100000 in" in segments[1].text


def test_inline_wrapper_preserves_later_source_lines():
    source = "open scoped Classical in def first : Nat := 1\n\ntheorem second : True := by trivial\n"

    _header, segments = segmentation.segment_file(source)

    assert [segment.start_line for segment in segments] == [1, 3]
    assert source[segments[1].start : segments[1].end] == segments[1].text


def test_segment_file_attaches_variable_wrapper_to_scoped_declaration():
    header, segments = segmentation.segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem first : True := by",
                "  trivial",
                "",
                "variable (P : Type) in",
                "abbrev Scoped := P",
                "",
                "theorem last : True := by",
                "  trivial",
                "",
            ]
        )
    )

    assert header == "import Mathlib\n"
    assert [segment.name for segment in segments] == ["first", "Scoped", "last"]
    assert "variable (P : Type) in" not in segments[0].text
    assert segments[1].text.startswith("variable (P : Type) in")
    assert segments[1].declaration_start > segments[1].start


def test_segment_file_attaches_open_scoped_wrapper_to_declaration():
    header, segments = segmentation.segment_file(
        "\n".join(
            [
                "import Mathlib",
                "",
                "theorem first : True := by",
                "  trivial",
                "",
                "open scoped Classical in",
                "def wrapped : Nat := 1",
                "",
                "theorem last : True := by",
                "  trivial",
                "",
            ]
        )
    )

    assert header == "import Mathlib\n"
    assert [segment.name for segment in segments] == ["first", "wrapped", "last"]
    assert "open scoped Classical in" not in segments[0].text
    assert segments[1].text.startswith("open scoped Classical in")
    assert segments[1].declaration_start > segments[1].start


def test_segment_file_normalizes_inline_open_scoped_abbrev():
    header, segments = segmentation.segment_file("import Mathlib\n\nopen scoped Classical in abbrev Wrapped := Nat\n")

    assert header == "import Mathlib\n\n"
    assert [segment.name for segment in segments] == ["Wrapped"]
    assert segments[0].text.startswith("open scoped Classical in ")
    assert segments[0].declaration_start > segments[0].start


@pytest.mark.parametrize(
    "source",
    [
        "import Std\n\nset_option maxRecDepth 10000 in\n"
        "set_option maxHeartbeats 500000 in\nprivate theorem a : True := by trivial\n"
        "open scoped Classical in def b : Nat := 1\n"
        "variable (P : Type) in\n/-- Alias. -/\nabbrev C := P\n",
        "namespace N\n/-- First. -/\n@[simp]\ntheorem a : True := by trivial\n"
        "set_option maxRecDepth 10000 in\n@[simp]\ntheorem b : True := by trivial\n"
        "mutual\ndef c : Nat := 1\ndef d : Nat := c\nend\n"
        "open scoped Classical in\ntheorem e : True := by trivial\nend N\n",
        'import Std\n\ndef a : String := "set_option x in"\n'
        "-- set_option maxRecDepth 10000 in\n"
        "theorem b : True := by trivial\n"
        "variable (P : Type) in abbrev C := P\n"
        "include P in\nomit P in\nexample : True := by trivial\n",
    ],
)
def test_scoped_wrapper_windows_preserve_full_prefix_repair(source):
    header, original = segmentation._segment_file_chunks(
        segmentation._normalize_inline_scoped_declarations(source), source_text=source
    )
    expected = list(original)
    expected_header = header
    for index, segment in enumerate(tuple(expected)):
        match = segmentation._SCOPED_COMMAND_WRAPPER_BEFORE_DECL_RE.search(source[: segment.start])
        if match is None:
            continue
        start = match.start(1)
        if index == 0:
            expected_header = source[:start]
        else:
            previous = expected[index - 1]
            expected[index - 1] = segmentation._rebuilt_segment(previous, source, start=previous.start, end=start)
        expected[index] = segmentation._rebuilt_segment(segment, source, start=start, end=segment.end)

    actual_header, actual = segmentation._repair_option_wrapped_segments(source, header, list(original))

    assert actual_header == expected_header
    assert actual == expected


def test_scoped_wrapper_repair_scans_bounded_declaration_windows(monkeypatch):
    source = "import Std\n\n" + "".join(
        ("set_option maxRecDepth 10000 in\n" if index % 17 == 0 else "") + f"theorem t{index} : True := by trivial\n\n"
        for index in range(500)
    )
    header, segments = segmentation._segment_file_chunks(source)
    pattern = segmentation._SCOPED_COMMAND_WRAPPER_BEFORE_DECL_RE
    searched = []

    class CountingPattern:
        def search(self, text, start=0, end=None):
            searched.append((len(text) if end is None else end) - start)
            return pattern.search(text, start, len(text) if end is None else end)

    monkeypatch.setattr(segmentation, "_SCOPED_COMMAND_WRAPPER_BEFORE_DECL_RE", CountingPattern())
    _header, repaired = segmentation._repair_option_wrapped_segments(source, header, segments)

    assert len(repaired) == 500
    assert len(searched) == 500
    assert sum(searched) <= 2 * len(source)
    assert max(searched) < 150


def test_native_segmentation_indexes_source_lines_once():
    class IndexedSource(str):
        def count(self, *args, **kwargs):
            pytest.fail("Declaration line numbers must not rescan every source prefix")

    source = IndexedSource(
        "import Std\n\n"
        + "".join(
            f"set_option maxRecDepth 10000 in\n/-- Item {index}. -/\ntheorem t{index} : True := by trivial\n\n"
            for index in range(100)
        )
    )

    _header, segments = segmentation.segment_file(source)

    assert len(segments) == 100
    assert [segment.start_line for segment in segments] == [3 + index * 4 for index in range(100)]
