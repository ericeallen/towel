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

SUMMARY = """\
**What Towel promises.** A change Towel makes preserves the program's results,
output, exceptions and order of side effects. This holds for programs that do
not use reflection or self-instrumentation, including through code they call.
Examples include reading frames, source or line numbers with `inspect.stack()`,
logging the caller with `%(funcName)s` and rewriting functions as pytest does
when it rewrites assertions. Mark functions that depend on these behaviors with
`# towel: no-extract`.

In relaxed mode, the default, the program must tolerate changes in when Python
frees objects it no longer uses. Attribute reads, indexing, comparisons,
arithmetic and truth tests must have no side effects, and repeated truth tests
must give the same answer. A module's functions, classes and imports must keep
their bindings while a helper runs. If a lambda uses only its own parameters, the
program must tolerate a change to its `__qualname__` when Towel moves it.
With `--cross-module`, code outside
the project must leave each module's builtin bindings alone, and the program
must not depend on the identity of equal literals from different modules.
`--strict` removes these relaxed-mode assumptions. A change that breaks a
program meeting these conditions is a bug.
"""

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


@pytest.mark.parametrize(
    "condition",
    ["repeated truth tests", "`__qualname__`", "builtin bindings", "identity of equal literals"],
)
def test_a_summary_missing_an_assumption_is_found(condition: str) -> None:
    """Documentation audit A5: each page must include all four easily missed conditions."""
    shortened = _compact(SUMMARY).replace(condition, "")
    assert not _states_the_summary(shortened)
