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

"""A method that never uses its receiver must not be given a need for one.

Python binds no receiver when a method is reached through its class, so
``Formatter.as_dollars(None, 1.5)`` is an ordinary call with ``self`` set to
``None``, and it works for as long as the body never reads an attribute of
``self``. Code does this to reuse a method's logic without building an
instance, most often in tests.

Extracting a block such methods share used to route it through
``self._extracted_func_0(...)``. Every genuine instance went on returning what
it always did, and that call became an ``AttributeError``: the transformation
added a dependency on the receiver that the method it rewrote did not have. No
checker reports it, because the signature always said ``self`` was a
``Formatter`` and a caller passing ``None`` was already outside that promise --
which is what makes it Towel's problem rather than the checker's.

The helper is now a ``staticmethod`` reached through the class. A method that
ignores its receiver has no dispatch to preserve, so nothing is lost by not
dispatching: across fourteen installed packages the fix moved no helper out of
its class (306 class-homed proposals against 305 before it, the one gained
where two methods disagreed about needing a receiver and the pair had been
refused for it). A block that does use the receiver takes it as an ordinary
argument instead.
"""

from __future__ import annotations

import ast
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


def test_a_method_reached_through_its_class_keeps_working(tmp_path: Path) -> None:
    """The defect itself: the answer must be the one the original program gave."""
    assert _outcome(UNBOUND) == (14, 17), "the fixture must work before the transformation"
    written, after = _refactored(tmp_path, UNBOUND)
    assert after == (14, 17), written


def test_the_helper_is_static_and_reached_through_the_class(tmp_path: Path) -> None:
    """Why it works: no receiver is asked for, and the helper stays where it belongs."""
    written, _ = _refactored(tmp_path, UNBOUND)
    helper = next(
        statement
        for node in ast.parse(written).body
        if isinstance(node, ast.ClassDef) and node.name == "A"
        for statement in node.body
        if isinstance(statement, ast.FunctionDef) and "extracted_func" in statement.name
    )
    decorators = [
        decorator.id for decorator in helper.decorator_list if isinstance(decorator, ast.Name)
    ]
    assert decorators == ["staticmethod"], written
    assert "self" not in [argument.arg for argument in helper.args.args], written
    assert f"A.{helper.name}(" in written and f"self.{helper.name}(" not in written, written


def test_a_method_that_uses_its_receiver_still_gets_an_instance_helper(tmp_path: Path) -> None:
    """The other side: a fix that made every helper static would pass the tests above."""
    assert _outcome(USES_RECEIVER) == (23, 26)
    written, after = _refactored(tmp_path, USES_RECEIVER)
    assert after == (23, 26), written
    helper = next(
        statement
        for node in ast.parse(written).body
        if isinstance(node, ast.ClassDef) and node.name == "A"
        for statement in node.body
        if isinstance(statement, ast.FunctionDef) and "extracted_func" in statement.name
    )
    assert not helper.decorator_list, written
    assert helper.args.args[0].arg == "self", written
    assert f"self.{helper.name}(" in written, written
