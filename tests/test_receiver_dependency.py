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

"""A method helper is reached through the receiver, which its method may not have needed.

Python lets a method be called through its class with anything in the
receiver's place, and such a call works whenever the body never reads ``self``:
``A.a(None, 3)`` is legal, and code does it to reuse a method's logic without
an instance. Extracting a block those methods share gives them a helper reached
as ``self._extracted_func_0(...)``, and that call becomes an ``AttributeError``.
The answer is unchanged for every genuine instance, and no checker reports it,
because the signature always said ``self`` was an ``A``.

**This is a documented limitation, and these tests pin it rather than forbid
it.** Declining method placement whenever the body never reads an attribute of
its receiver does remove the breakage, and it was measured: across fourteen
installed packages (Towel, Black, Click, Bandit, coverage, Hypothesis, urllib3,
requests, Pygments, Rich, mypy, packaging, virtualenv, pip) it cost 57 of 305
class-homed proposals, a fifth of them, which fall back to module-level
helpers. That is a loss every user sees, traded against a call pattern almost
none writes, so the rule was not kept.

The repair that costs nothing is a different one: a method that ignores its
receiver has no dispatch to preserve, so its helper could be a ``staticmethod``
reached through the class rather than through ``self``. That is new machinery
and is not in this release.

If a change makes these tests fail, the limitation has been fixed --
delete them and say so in `docs/KNOWN_LIMITATIONS.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from towel.unification.refactor_engine import UnificationRefactorEngine

_SHARED_BLOCK = (
    "        first = value + 1\n        second = first * 2\n        third = second + 5\n"
)

UNBOUND = (
    "class A:\n"
    "    def a(self, value):\n" + _SHARED_BLOCK + "        return third + 1\n\n"
    "    def b(self, value):\n" + _SHARED_BLOCK + "        return third + 2\n\n\n"
    "def main():\n    return A.a(None, 3), A.b(None, 4)\n"
)

USES_RECEIVER = (
    "class A:\n"
    "    def __init__(self):\n        self.offset = 10\n\n"
    "    def a(self, value):\n" + _SHARED_BLOCK + "        return third + self.offset\n\n"
    "    def b(self, value):\n" + _SHARED_BLOCK + "        return third + self.offset + 1\n\n\n"
    "def main():\n    return A().a(3), A().b(4)\n"
)


def _outcome(source: str) -> Any:
    """What ``main`` returns when ``source`` runs, or the exception that stops it."""
    namespace: Dict[str, Any] = {}
    try:
        exec(compile(source, "<program>", "exec"), namespace)  # noqa: S102 - the oracle
        return namespace["main"]()
    except Exception as failure:  # The comparison is the point; the kind is the evidence.
        return f"{type(failure).__name__}: {failure}"


def _refactored(tmp_path: Path, source: str) -> Tuple[str, Any]:
    """Towel's output for ``source``, run the way ``towel dry`` runs it, and what it does."""
    project = tmp_path / "input"
    project.mkdir()
    (project / "program.py").write_text(source)
    output = tmp_path / "output"
    UnificationRefactorEngine().refactor_directory_to_fixed_point(
        str(project), str(output), progress="none"
    )
    written = (output / "program.py").read_text()
    return written, _outcome(written)


def test_an_unbound_call_with_no_receiver_stops_working_and_this_is_known(
    tmp_path: Path,
) -> None:
    """The limitation itself, executable, so that fixing it cannot pass unnoticed."""
    assert _outcome(UNBOUND) == (14, 17), "the fixture must work before the transformation"
    written, after = _refactored(tmp_path, UNBOUND)
    assert isinstance(after, str) and after.startswith("AttributeError"), written


def test_every_genuine_instance_still_gets_the_same_answer(tmp_path: Path) -> None:
    """The limitation's boundary: nothing called on an actual instance is affected."""
    assert _outcome(USES_RECEIVER) == (23, 26)
    written, after = _refactored(tmp_path, USES_RECEIVER)
    assert after == (23, 26), written
