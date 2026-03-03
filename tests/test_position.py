"""Tests for ``kgn.lsp.position`` — SourceMap and PositionAdapter.

Covers:
* SourceMap: offset ↔ (line, col) roundtrip with ASCII, Korean, emoji,
  mixed content, single-line, multi-line, empty, and large documents.
* PositionAdapter: UTF-8 (code-point) ↔ UTF-16 (code-unit) column
  conversion including BMP characters, supplementary-plane emoji, and
  Korean text.
"""

from __future__ import annotations

import pytest

from kgn.lsp.position import PositionAdapter, SourceMap

# ═══════════════════════════════════════════════════════════════════════
# SourceMap
# ═══════════════════════════════════════════════════════════════════════


class TestSourceMapBasic:
    """Core offset ↔ position conversion."""

    def test_empty_text(self) -> None:
        sm = SourceMap("")
        assert sm.line_count == 1  # One (empty) line
        assert sm.offset_to_position(0) == (0, 0)

    def test_single_line_no_newline(self) -> None:
        sm = SourceMap("hello")
        assert sm.line_count == 1
        assert sm.offset_to_position(0) == (0, 0)
        assert sm.offset_to_position(4) == (0, 4)
        assert sm.offset_to_position(5) == (0, 5)  # end

    def test_single_line_with_newline(self) -> None:
        sm = SourceMap("hello\n")
        assert sm.line_count == 2
        assert sm.offset_to_position(5) == (0, 5)  # the \n itself
        assert sm.offset_to_position(6) == (1, 0)  # after \n

    def test_two_lines(self) -> None:
        sm = SourceMap("ab\ncd")
        assert sm.line_count == 2
        assert sm.offset_to_position(0) == (0, 0)  # 'a'
        assert sm.offset_to_position(1) == (0, 1)  # 'b'
        assert sm.offset_to_position(2) == (0, 2)  # '\n'
        assert sm.offset_to_position(3) == (1, 0)  # 'c'
        assert sm.offset_to_position(4) == (1, 1)  # 'd'

    def test_three_lines(self) -> None:
        sm = SourceMap("a\nbb\nccc")
        assert sm.line_count == 3
        assert sm.offset_to_position(0) == (0, 0)
        assert sm.offset_to_position(2) == (1, 0)
        assert sm.offset_to_position(5) == (2, 0)
        assert sm.offset_to_position(7) == (2, 2)

    def test_trailing_newline(self) -> None:
        sm = SourceMap("abc\n")
        assert sm.line_count == 2
        assert sm.offset_to_position(4) == (1, 0)

    def test_consecutive_newlines(self) -> None:
        sm = SourceMap("\n\n\n")
        assert sm.line_count == 4
        assert sm.offset_to_position(0) == (0, 0)
        assert sm.offset_to_position(1) == (1, 0)
        assert sm.offset_to_position(2) == (2, 0)
        assert sm.offset_to_position(3) == (3, 0)


class TestSourceMapRoundtrip:
    """offset → position → offset roundtrip."""

    @pytest.mark.parametrize(
        "text",
        [
            "hello world",
            "line1\nline2\nline3",
            "\n\n\n",
            "a\nbb\nccc\ndddd",
            "",
            "x",
        ],
    )
    def test_roundtrip(self, text: str) -> None:
        sm = SourceMap(text)
        for offset in range(len(text) + 1):
            line, col = sm.offset_to_position(offset)
            recovered = sm.position_to_offset(line, col)
            assert recovered == offset, f"offset={offset}, line={line}, col={col}"


class TestSourceMapPositionToOffset:
    """position_to_offset specific tests."""

    def test_basic(self) -> None:
        sm = SourceMap("ab\ncd\nef")
        assert sm.position_to_offset(0, 0) == 0
        assert sm.position_to_offset(0, 1) == 1
        assert sm.position_to_offset(1, 0) == 3
        assert sm.position_to_offset(1, 1) == 4
        assert sm.position_to_offset(2, 0) == 6
        assert sm.position_to_offset(2, 1) == 7

    def test_clamping_line_too_large(self) -> None:
        sm = SourceMap("ab\ncd")
        offset = sm.position_to_offset(999, 0)
        # Clamped to last line start
        assert offset == 3  # line 1 start

    def test_clamping_col_beyond_text(self) -> None:
        sm = SourceMap("ab")
        offset = sm.position_to_offset(0, 999)
        assert offset == 2  # clamped to text length

    def test_negative_values_clamped(self) -> None:
        sm = SourceMap("abc")
        assert sm.position_to_offset(-5, -5) == 0


class TestSourceMapLineStart:
    """line_start_offset tests."""

    def test_first_line(self) -> None:
        sm = SourceMap("abc\ndef")
        assert sm.line_start_offset(0) == 0

    def test_second_line(self) -> None:
        sm = SourceMap("abc\ndef")
        assert sm.line_start_offset(1) == 4

    def test_clamped(self) -> None:
        sm = SourceMap("abc")
        assert sm.line_start_offset(100) == 0  # clamped to line 0


class TestSourceMapKorean:
    """Korean characters (3-byte UTF-8, 1 code point)."""

    def test_korean_offset(self) -> None:
        text = "가나다\n라마바"
        sm = SourceMap(text)
        assert sm.line_count == 2
        # '가' at offset 0, '나' at offset 1, '다' at offset 2
        assert sm.offset_to_position(0) == (0, 0)
        assert sm.offset_to_position(1) == (0, 1)
        assert sm.offset_to_position(2) == (0, 2)
        # '\n' at offset 3
        assert sm.offset_to_position(3) == (0, 3)
        # '라' at offset 4
        assert sm.offset_to_position(4) == (1, 0)


class TestSourceMapEmoji:
    """Emoji characters (supplementary plane, 4-byte UTF-8, 2 UTF-16 code units)."""

    def test_emoji_offset(self) -> None:
        # 😀 is U+1F600 — 1 code point in Python str
        text = "a😀b\nc"
        sm = SourceMap(text)
        assert sm.line_count == 2
        assert sm.offset_to_position(0) == (0, 0)  # 'a'
        assert sm.offset_to_position(1) == (0, 1)  # '😀'
        assert sm.offset_to_position(2) == (0, 2)  # 'b'
        assert sm.offset_to_position(3) == (0, 3)  # '\n'
        assert sm.offset_to_position(4) == (1, 0)  # 'c'


# ═══════════════════════════════════════════════════════════════════════
# PositionAdapter — UTF-8 (code-point) ↔ UTF-16
# ═══════════════════════════════════════════════════════════════════════


class TestPositionAdapterAscii:
    """ASCII text: code-point col == UTF-16 col."""

    def test_zero(self) -> None:
        assert PositionAdapter.utf8_col_to_utf16("hello", 0) == 0

    def test_middle(self) -> None:
        assert PositionAdapter.utf8_col_to_utf16("hello", 3) == 3

    def test_end(self) -> None:
        assert PositionAdapter.utf8_col_to_utf16("hello", 5) == 5

    def test_reverse_zero(self) -> None:
        assert PositionAdapter.utf16_col_to_utf8("hello", 0) == 0

    def test_reverse_middle(self) -> None:
        assert PositionAdapter.utf16_col_to_utf8("hello", 3) == 3

    def test_reverse_end(self) -> None:
        assert PositionAdapter.utf16_col_to_utf8("hello", 5) == 5


class TestPositionAdapterKorean:
    """Korean text (BMP): 1 code point = 1 UTF-16 code unit = 3 UTF-8 bytes."""

    def test_korean_forward(self) -> None:
        line = "가나다"
        # Code-point col 0 → UTF-16 col 0
        assert PositionAdapter.utf8_col_to_utf16(line, 0) == 0
        # Code-point col 1 → UTF-16 col 1
        assert PositionAdapter.utf8_col_to_utf16(line, 1) == 1
        # Code-point col 3 → UTF-16 col 3
        assert PositionAdapter.utf8_col_to_utf16(line, 3) == 3

    def test_korean_reverse(self) -> None:
        line = "가나다"
        assert PositionAdapter.utf16_col_to_utf8(line, 0) == 0
        assert PositionAdapter.utf16_col_to_utf8(line, 1) == 1
        assert PositionAdapter.utf16_col_to_utf8(line, 3) == 3


class TestPositionAdapterEmoji:
    """Supplementary-plane emoji: 1 code point = 2 UTF-16 code units."""

    def test_emoji_forward(self) -> None:
        # "a😀b" — 'a' at cp 0, '😀' at cp 1, 'b' at cp 2
        line = "a😀b"
        assert PositionAdapter.utf8_col_to_utf16(line, 0) == 0  # before 'a'
        assert PositionAdapter.utf8_col_to_utf16(line, 1) == 1  # before '😀'
        assert PositionAdapter.utf8_col_to_utf16(line, 2) == 3  # after '😀' (2 units)
        assert PositionAdapter.utf8_col_to_utf16(line, 3) == 4  # after 'b'

    def test_emoji_reverse(self) -> None:
        line = "a😀b"
        assert PositionAdapter.utf16_col_to_utf8(line, 0) == 0  # 'a'
        assert PositionAdapter.utf16_col_to_utf8(line, 1) == 1  # '😀' start
        # UTF-16 col 2 is the middle of the surrogate pair — still maps to '😀'
        # But since we iterate by code points, col 2 would not be an exact
        # boundary.  The specification says col 3 = after emoji.
        assert PositionAdapter.utf16_col_to_utf8(line, 3) == 2  # after '😀'
        assert PositionAdapter.utf16_col_to_utf8(line, 4) == 3  # after 'b'

    def test_multiple_emoji(self) -> None:
        line = "😀😀"
        # cp 0 → utf16 0
        assert PositionAdapter.utf8_col_to_utf16(line, 0) == 0
        # cp 1 → utf16 2
        assert PositionAdapter.utf8_col_to_utf16(line, 1) == 2
        # cp 2 → utf16 4
        assert PositionAdapter.utf8_col_to_utf16(line, 2) == 4


class TestPositionAdapterMixed:
    """Mixed ASCII + Korean + emoji."""

    def test_mixed_forward(self) -> None:
        # "a가😀b" — 'a'(1), '가'(1), '😀'(2), 'b'(1) UTF-16 units
        line = "a가😀b"
        assert PositionAdapter.utf8_col_to_utf16(line, 0) == 0  # before 'a'
        assert PositionAdapter.utf8_col_to_utf16(line, 1) == 1  # before '가'
        assert PositionAdapter.utf8_col_to_utf16(line, 2) == 2  # before '😀'
        assert PositionAdapter.utf8_col_to_utf16(line, 3) == 4  # before 'b'
        assert PositionAdapter.utf8_col_to_utf16(line, 4) == 5  # after 'b'

    def test_mixed_reverse(self) -> None:
        line = "a가😀b"
        assert PositionAdapter.utf16_col_to_utf8(line, 0) == 0  # 'a'
        assert PositionAdapter.utf16_col_to_utf8(line, 1) == 1  # '가'
        assert PositionAdapter.utf16_col_to_utf8(line, 2) == 2  # '😀' start
        assert PositionAdapter.utf16_col_to_utf8(line, 4) == 3  # 'b'
        assert PositionAdapter.utf16_col_to_utf8(line, 5) == 4  # end


class TestPositionAdapterRoundtrip:
    """Roundtrip: cp → utf16 → cp must be identity at valid boundaries."""

    @pytest.mark.parametrize(
        "line",
        [
            "hello world",
            "가나다라",
            "😀🎉🚀",
            "a가😀b나🎉c",
            "",
            "x",
        ],
    )
    def test_roundtrip_at_valid_boundaries(self, line: str) -> None:
        for cp_col in range(len(line) + 1):
            utf16 = PositionAdapter.utf8_col_to_utf16(line, cp_col)
            recovered = PositionAdapter.utf16_col_to_utf8(line, utf16)
            assert recovered == cp_col, (
                f"line={line!r}, cp_col={cp_col}, utf16={utf16}, recovered={recovered}"
            )


class TestPositionAdapterEdgeCases:
    """Edge cases for PositionAdapter."""

    def test_empty_string(self) -> None:
        assert PositionAdapter.utf8_col_to_utf16("", 0) == 0
        assert PositionAdapter.utf16_col_to_utf8("", 0) == 0

    def test_utf16_col_beyond_end(self) -> None:
        """Column beyond the line end returns len(line)."""
        assert PositionAdapter.utf16_col_to_utf8("ab", 999) == 2

    def test_utf8_col_at_end(self) -> None:
        assert PositionAdapter.utf8_col_to_utf16("ab", 2) == 2


# ═══════════════════════════════════════════════════════════════════════
# PositionAdapter — utf8_byte_col_to_codepoint
# ═══════════════════════════════════════════════════════════════════════


class TestByteColToCodepoint:
    """Convert raw UTF-8 byte offsets to code-point columns."""

    def test_ascii(self) -> None:
        line = "hello"
        line_bytes = line.encode("utf-8")
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 0) == 0
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 3) == 3

    def test_korean(self) -> None:
        line = "가나다"
        line_bytes = line.encode("utf-8")
        # '가' = 3 bytes, '나' starts at byte 3
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 0) == 0
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 3) == 1
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 6) == 2
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 9) == 3

    def test_emoji(self) -> None:
        line = "😀"
        line_bytes = line.encode("utf-8")
        # '😀' = 4 bytes
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 0) == 0
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 4) == 1

    def test_mixed(self) -> None:
        line = "a가😀"
        line_bytes = line.encode("utf-8")
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 0) == 0  # 'a'
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 1) == 1  # '가'
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 4) == 2  # '😀'
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 8) == 3  # end

    def test_clamped_beyond_end(self) -> None:
        line_bytes = b"abc"
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, 999) == 3

    def test_clamped_negative(self) -> None:
        line_bytes = b"abc"
        assert PositionAdapter.utf8_byte_col_to_codepoint(line_bytes, -1) == 0
