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

"""The facts the method-helper hosting rule rests on, held as executable evidence.

docs/DECISIONS.md ("A method helper lives in the class that holds both
duplicates") keeps a helper in a class only when every duplicate is a method
of that class, names it class-private, and sends every other shared block to a
module function that takes the receiver. The justification is a set of claims
about Python and about the two checkers. Each is asserted here, so a Python or
checker release that changes one fails this file and sends the decision back
for review, instead of leaving it resting on something no longer true.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import List

import pytest

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

# A class-private helper beside a subclass that defines the same name with an
# incompatible signature: the shape the rule relies on.
CLASS_PRIVATE = """
    class A:
        def __init__(self) -> None:
            self._cache = 1

        def total(self, n: int) -> int:
            return self.__helper(n)

        def __helper(self, n: int) -> int:
            return self._cache + n


    class B(A):
        def __helper(self, n: str) -> str:
            return n

        def other(self) -> str:
            return self.__helper("b")
    """


def _write(tmp_path: Path, source: str, *, strict_pyright: bool = False) -> Path:
    path = tmp_path / "subject.py"
    header = "# pyright: strict\n" if strict_pyright else ""
    path.write_text(header + textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _mypy_errors(path: Path) -> List[str]:
    """mypy's errors for ``path``; an empty list only when mypy ran and passed it."""
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", str(path)],
        capture_output=True,
        text=True,
        cwd=path.parent,
        timeout=300,
    )
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    # A checker that did not run reports no errors either; tell the two apart.
    assert result.returncode == (1 if errors else 0), result.stdout + result.stderr
    assert errors or "Success: no issues found in 1 source file" in result.stdout, result.stdout
    return errors


def _pyright_errors(path: Path) -> List[str]:
    """pyright's errors for ``path``; an empty list only when pyright ran and passed it."""
    result = subprocess.run(
        [sys.executable, "-m", "pyright", str(path)],
        capture_output=True,
        text=True,
        cwd=path.parent,
        timeout=300,
    )
    errors = [line for line in result.stdout.splitlines() if " - error:" in line]
    assert result.returncode == (1 if errors else 0), result.stdout + result.stderr
    assert f"{len(errors)} error" in result.stdout, result.stdout
    return errors


def test_a_subclass_cannot_override_a_class_private_helper(tmp_path: Path) -> None:
    """``__helper`` in ``A`` is stored as ``_A__helper``; ``B``'s is ``_B__helper``.

    A helper named that way is out of reach of every subclass, including ones
    outside the project that Towel cannot see: ``A``'s own methods keep
    calling ``A``'s helper whatever a subclass defines.
    """
    namespace: dict[str, object] = {}
    exec(compile(textwrap.dedent(CLASS_PRIVATE), "subject.py", "exec"), namespace)
    subclass = namespace["B"]
    assert subclass().total(1) == 2  # type: ignore[operator]
    assert subclass().other() == "b"  # type: ignore[operator]


@requires_mypy
def test_mypy_accepts_a_class_private_helper_beside_a_same_named_subclass_method(
    tmp_path: Path,
) -> None:
    assert _mypy_errors(_write(tmp_path, CLASS_PRIVATE)) == []


@requires_pyright
def test_pyright_strict_accepts_a_class_private_helper_beside_a_same_named_subclass_method(
    tmp_path: Path,
) -> None:
    assert _pyright_errors(_write(tmp_path, CLASS_PRIVATE, strict_pyright=True)) == []


MANGLED_OUTSIDE = """
    class A:
        def __init__(self) -> None:
            self.__secret = 2

        def get(self) -> int:
            return self.__secret


    def moved(receiver: A) -> int:
        return receiver._A__secret
    """


def test_a_mangled_name_spelled_outside_its_class_runs(tmp_path: Path) -> None:
    namespace: dict[str, object] = {}
    exec(compile(textwrap.dedent(MANGLED_OUTSIDE), "subject.py", "exec"), namespace)
    assert namespace["moved"](namespace["A"]()) == 2  # type: ignore[operator]


@requires_mypy
def test_mypy_rejects_a_mangled_name_spelled_outside_its_class(tmp_path: Path) -> None:
    """Why private attributes cannot move to a module function in a checkable form."""
    errors = _mypy_errors(_write(tmp_path, MANGLED_OUTSIDE))
    assert any('has no attribute "_A__secret"' in error for error in errors), errors


@requires_pyright
def test_pyright_rejects_a_mangled_name_spelled_outside_its_class(tmp_path: Path) -> None:
    errors = _pyright_errors(_write(tmp_path, MANGLED_OUTSIDE))
    assert any("_A__secret" in error for error in errors), errors


PROTECTED_OUTSIDE = """
    class A:
        def __init__(self) -> None:
            self._cache = 1


    def moved(receiver: A) -> int:
        return receiver._cache
    """


@requires_pyright
def test_pyright_strict_rejects_a_module_function_reading_a_protected_attribute(
    tmp_path: Path,
) -> None:
    """Why a same-class helper stays a method: outside the class, strict pyright rejects it."""
    errors = _pyright_errors(_write(tmp_path, PROTECTED_OUTSIDE, strict_pyright=True))
    assert any("reportPrivateUsage" in error for error in errors), errors


RECEIVERS = """
    from types import SimpleNamespace
    from typing import Protocol


    class HasV(Protocol):
        v: int


    class A:
        def __init__(self) -> None:
            self.v = 1

        def implicit(self, n: int) -> int:
            return self.v + n

        def declared(self: HasV, n: int) -> int:
            return self.v + n


    class B:
        v: int = 3


    A.implicit(SimpleNamespace(v=10), 1)
    A.implicit(B(), 1)
    A.declared(B(), 1)
    """


@requires_mypy
def test_mypy_types_a_receiver_as_its_class_unless_the_method_declares_otherwise(
    tmp_path: Path,
) -> None:
    """A non-instance receiver is outside the contract; a declared self type is honoured."""
    errors = _mypy_errors(_write(tmp_path, RECEIVERS))
    assert len(errors) == 2, errors
    assert all('to "implicit" of "A" has incompatible type' in error for error in errors), errors


@requires_pyright
def test_pyright_types_a_receiver_as_its_class_unless_the_method_declares_otherwise(
    tmp_path: Path,
) -> None:
    errors = _pyright_errors(_write(tmp_path, RECEIVERS))
    assert len(errors) == 2, errors
    assert all('parameter "self" of type "A"' in error for error in errors), errors
