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

"""Audit CLI-02: library pairing failures must clear their transient display."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from towel.unification import progress, refactor_engine
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.block_signature import BlockSignature, quick_filter


class _TerminalStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_library_pairing_exception_clears_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit CLI-02: an exception after pairing starts leaves no stale bar to redraw."""
    source = tmp_path / "module.py"
    body = "    x = 1\n    y = x + 2\n    return y\n"
    source.write_text("\n".join(f"def {name}():\n{body}" for name in ("first", "second", "third")))
    output = _TerminalStream()

    def fail_after_first_pair(first: BlockSignature, second: BlockSignature) -> bool:
        if "Pairing blocks:" in output.getvalue():
            raise RuntimeError("pairing failed after drawing")
        return quick_filter(first, second)

    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    monkeypatch.setattr(refactor_engine, "quick_filter", fail_after_first_pair)
    with contextlib.redirect_stderr(output):
        try:
            with pytest.raises(RuntimeError, match="pairing failed after drawing"):
                UnificationRefactorEngine(min_lines=3).analyze_files([str(source)])
            assert not progress._ACTIVE
            assert output.getvalue().endswith("\r\x1b[2K"), repr(output.getvalue())
            assert "\n" not in output.getvalue()
        finally:
            # A failing regression must not leak a display into the other tests.
            for renderer in tuple(progress._ACTIVE):
                renderer.close()
