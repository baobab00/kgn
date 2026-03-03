"""Source-position utilities for the KGN LSP server.

Provides two core abstractions:

* :class:`SourceMap` — O(log n) bidirectional mapping between character
  offsets and (line, col) positions within a text document.
* :class:`PositionAdapter` — UTF-8 ↔ UTF-16 column conversion required
  by the LSP specification (``PositionEncodingKind.UTF16``).

Design rationale
----------------
The LSP protocol uses **0-based line** and **UTF-16 code-unit** column
offsets.  Python strings are internally UCS-4 / UTF-8, so every column
value exchanged with the client must be converted.  This module
centralises that logic so the rest of the server sees a clean API.

Key edge cases:
* **Korean (AC00–D7AF)**: 3 bytes in UTF-8, 1 code unit in UTF-16.
* **Emoji / supplementary plane (above U+FFFF)**: 4 bytes in UTF-8,
  **2** code units (surrogate pair) in UTF-16.
* **ASCII (00–7F)**: 1 byte = 1 code unit — no conversion needed.
"""

from __future__ import annotations

import bisect


class SourceMap:
    """Bidirectional offset ↔ (line, col) mapping for a text document.

    All values are **0-based**.  Column values are *character* offsets
    within the line (Python ``str`` indexing, i.e. Unicode code points).

    Construction is O(n) in the document length; subsequent lookups are
    O(log n) via :mod:`bisect`.

    Parameters:
        text: Full document text.  Line endings are always ``\\n``.
    """

    __slots__ = ("_line_offsets", "_text_length")

    def __init__(self, text: str) -> None:
        offsets: list[int] = [0]
        idx = 0
        while True:
            idx = text.find("\n", idx)
            if idx == -1:
                break
            idx += 1  # position after the newline
            offsets.append(idx)
        self._line_offsets: list[int] = offsets
        self._text_length: int = len(text)

    @property
    def line_count(self) -> int:
        """Total number of lines in the document."""
        return len(self._line_offsets)

    def offset_to_position(self, offset: int) -> tuple[int, int]:
        """Convert a character *offset* to a 0-based ``(line, col)`` pair.

        If *offset* exceeds the document length, it is clamped to the
        last valid position.

        Returns:
            ``(line, col)`` — both 0-based.
        """
        offset = max(0, min(offset, self._text_length))
        line = bisect.bisect_right(self._line_offsets, offset) - 1
        col = offset - self._line_offsets[line]
        return line, col

    def position_to_offset(self, line: int, col: int) -> int:
        """Convert a 0-based ``(line, col)`` pair to a character offset.

        Out-of-range values are clamped.

        Returns:
            Character offset (0-based).
        """
        line = max(0, min(line, len(self._line_offsets) - 1))
        offset = self._line_offsets[line] + max(0, col)
        return min(offset, self._text_length)

    def line_start_offset(self, line: int) -> int:
        """Return the character offset where *line* begins."""
        line = max(0, min(line, len(self._line_offsets) - 1))
        return self._line_offsets[line]


class PositionAdapter:
    """Convert column offsets between UTF-8 / UTF-16 / Unicode code points.

    The LSP default encoding is ``UTF-16`` — clients send column numbers
    counted in UTF-16 code units.  Python uses Unicode code points for
    string indexing.  This class bridges the gap.

    All methods are **static** — no instance state is needed.
    """

    @staticmethod
    def utf8_col_to_utf16(line_text: str, codepoint_col: int) -> int:
        """Convert a **Unicode code-point** column to a **UTF-16 code-unit** column.

        Parameters:
            line_text: The full text of the line (without trailing newline).
            codepoint_col: 0-based index of the character within
                *line_text* as a Python ``str`` (i.e. code-point offset,
                which is identical to the UTF-32 column).

        Returns:
            The equivalent 0-based UTF-16 column.
        """
        utf16_col = 0
        for i, ch in enumerate(line_text):
            if i >= codepoint_col:
                break
            utf16_col += 2 if ord(ch) > 0xFFFF else 1
        return utf16_col

    @staticmethod
    def utf16_col_to_utf8(line_text: str, utf16_col: int) -> int:
        """Convert a **UTF-16 code-unit** column to a **Unicode code-point** column.

        Parameters:
            line_text: The full text of the line (without trailing newline).
            utf16_col: 0-based column in UTF-16 code units.

        Returns:
            The equivalent 0-based code-point column.
        """
        consumed_units = 0
        for i, ch in enumerate(line_text):
            if consumed_units >= utf16_col:
                return i
            consumed_units += 2 if ord(ch) > 0xFFFF else 1
        # If we consumed all characters without reaching utf16_col,
        # return the end-of-line position.
        return len(line_text)

    @staticmethod
    def utf8_byte_col_to_codepoint(line_bytes: bytes, byte_col: int) -> int:
        """Convert a raw UTF-8 byte offset to a code-point column.

        Useful when an external tool (e.g. ``yaml.compose()``) reports
        column positions in bytes rather than characters.

        Parameters:
            line_bytes: The line encoded as UTF-8.
            byte_col: 0-based byte offset within *line_bytes*.

        Returns:
            0-based code-point column.
        """
        byte_col = max(0, min(byte_col, len(line_bytes)))
        return len(line_bytes[:byte_col].decode("utf-8", errors="replace"))
