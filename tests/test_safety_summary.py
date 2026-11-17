# Copyright 2026 Eric Allen
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

"""Every page that states what Towel promises states it in the same words.

Two cold readers of the 2.0 pages (Codex and Gemini, 2026-11-17) found the
promise told differently on each page: the README named one of relaxed mode's
three assumptions, the relaxed-and-strict page all three, and the validation,
comparison and help pages none, nor the reflection boundary. One summary, kept
in step here, names all of them wherever the promise is made.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]

SUMMARY = (
    "**What Towel promises.** A change Towel makes keeps what your program computes and does:"
    " its results, output, exceptions and the order of its side effects. That holds for"
    " programs that do not use reflection or self-instrumentation, including through code"
    " they call. Reflection here means reading frames, source or line numbers, as"
    " `inspect.stack()` does, or a log format with `%(funcName)s`; self-instrumentation means"
    " a tool that rewrites functions, such as pytest's assertion rewriting. Mark a function"
    " that depends on these with `# towel: no-extract`. In relaxed mode, the default, Towel"
    " also assumes that your program does not depend on when Python frees an object it no"
    " longer uses; that reading an attribute, indexing, comparing and arithmetic have no side"
    " effects; and that a module's functions, classes and imports are not rebound while a"
    " helper runs, as a test's monkeypatch can do. `--strict` makes none of these three"
    " assumptions. A change that breaks a program meeting these conditions is a bug."
)

PAGES = (
    "README.md",
    "docs/RELAXED_AND_STRICT.md",
    "docs/FAQ.md",
    "docs/KNOWN_LIMITATIONS.md",
    "docs/GENERATED_CODE.md",
    "docs/VALIDATION.md",
    "docs/COMPARISON.md",
    "docs/GETTING_HELP.md",
)


def _compact(text: str) -> str:
    return " ".join(text.split())


def _states_the_summary(text: str) -> bool:
    return _compact(SUMMARY) in _compact(text)


@pytest.mark.parametrize("page", PAGES)
def test_the_page_states_the_one_summary(page: str) -> None:
    assert _states_the_summary((ROOT / page).read_text(encoding="utf-8"))


def test_a_page_that_makes_the_promise_uses_the_summary() -> None:
    """A user page that words the promise its own way is found."""
    promise = re.compile(r"programs? that do(?:es)? not use reflection", re.IGNORECASE)
    pages = ["README.md", *(f"docs/{path.name}" for path in sorted((ROOT / "docs").glob("*.md")))]
    stray = [
        page
        for page in pages
        if promise.search(
            _compact((ROOT / page).read_text(encoding="utf-8")).replace(_compact(SUMMARY), "")
        )
    ]
    assert stray == []


def test_a_summary_missing_an_assumption_is_found() -> None:
    shortened = SUMMARY.replace(
        "; that reading an attribute, indexing, comparing and arithmetic have no side effects", ""
    )
    assert not _states_the_summary(shortened)
