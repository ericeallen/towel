# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runtime audit item 8: changing code must not normalize untouched line endings."""

from towel.source_text import decode_source, encode_like


def test_mixed_endings_roundtrip_line_by_line() -> None:
    """Runtime item 8: even a no-op used to normalize mixed line endings."""
    original = b"a = 1\r\nb = 2\nc = 3\r\nd = 4\r"
    assert encode_like(original, decode_source(original)) == original


def test_inserted_and_replaced_lines_preserve_surrounding_endings() -> None:
    """Runtime item 8: unchanged lines retain endings across inserts and rewrites."""
    original = b"first = 1\r\nsecond = 2\nthird = 3\r\nlast = 4\n"
    text = "new = 0\nfirst = 1\nsecond = 20\nthird = 3\nlast = 4\n"
    assert encode_like(original, text) == (
        b"new = 0\nfirst = 1\r\nsecond = 20\nthird = 3\r\nlast = 4\n"
    )


def test_mixed_endings_keep_cookie_and_bom() -> None:
    """Runtime item 8: restoring endings does not alter encoding or final-newline absence."""
    original = b"\xef\xbb\xbfa = 1\r\nb = 2\nc = 3"
    assert encode_like(original, "a = 1\nb = 20\nc = 3") == (b"\xef\xbb\xbfa = 1\r\nb = 20\nc = 3")
