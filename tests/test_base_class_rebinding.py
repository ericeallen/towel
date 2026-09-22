"""A base-class name means whatever the module last bound it to, not whatever class wears it.

Towel hoists a helper two methods share into the class their classes have in
common. Finding that class used to be a name lookup over the whole module:
the class statement spelling ``Base`` was taken to be what ``Base`` denotes
wherever the name is written. Python binds module globals as the module
runs, so an ordinary assignment between two class statements makes the name
denote two different objects at the two points that read it, and the second
subclass then never inherits from the class the helper was put in. The
program Towel wrote raised ``AttributeError`` where the program it was given
returned an answer.

Nothing exotic is needed to arrange that, which is why these tests exist:
``Base = object`` and a plain ``if`` are ordinary Python, and a reader of
Towel's documented limitations, which speak of reflection and of rebinding
concurrent with a run, would not expect either to be excluded. Towel's
promise is that the program it writes behaves as the program it was given,
so the oracle here is the programs themselves: each is executed before and
after, and the two results must agree. Inspecting the generated source would
only restate whatever the placement rule happens to do.

Declining is not free, so the last test holds the other side down. An
ordinary base class, never rebound, must still receive the shared helper as
a method; a fix that answered "cannot tell" everywhere would pass the first
two tests and quietly turn base-class placement off.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Tuple

from towel.unification.refactor_engine import UnificationRefactorEngine

# Three lines, the default minimum, duplicated between the two subclasses.
_SHARED_BLOCK = (
    "        first = value + 1\n        second = first * 2\n        third = second + 5\n"
)


def _subclass(name: str, base: str, tail: int) -> str:
    """A subclass whose method reads its receiver, so the helper is an instance method.

    A method that never touches ``self`` gets a static helper reached through
    the class instead (see `test_receiver_dependency.py`), which would make
    these tests silent about the base they were written to be about.
    """
    return (
        f"class {name}({base}):\n"
        "    offset = 0\n\n"
        "    def compute(self, value):\n"
        + _SHARED_BLOCK
        + f"        return third + {tail} + self.offset\n\n\n"
    )


_ENTRY_POINT = "def main():\n    return Alpha().compute(3), Beta().compute(4)\n"


def _outcome(source: str) -> Any:
    """What ``main`` returns when ``source`` runs, or the exception that stops it."""
    namespace: Dict[str, Any] = {}
    try:
        exec(compile(source, "<program>", "exec"), namespace)
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


def _helper_names(source: str, class_name: str) -> list[str]:
    """The extracted helpers defined directly in ``class_name``'s body."""
    return [
        statement.name
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
        for statement in node.body
        if isinstance(statement, ast.FunctionDef) and "extracted_func" in statement.name
    ]


def test_a_base_rebound_by_assignment_between_its_subclasses_keeps_its_meaning(
    tmp_path: Path,
) -> None:
    """``Base = object`` between the two class statements gives them different bases.

    ``Alpha`` inherits the class written above it; ``Beta``, defined after the
    assignment, inherits ``object`` and shares no ancestor with ``Alpha`` but
    ``object`` itself. A helper placed in the class statement named ``Base``
    is therefore reachable from ``Alpha`` and invisible to ``Beta``.
    """
    source = (
        "class Base:\n    pass\n\n\n"
        + _subclass("Alpha", "Base", 1)
        + "Base = object\n\n\n"
        + _subclass("Beta", "Base", 2)
        + _ENTRY_POINT
    )
    written, after = _refactored(tmp_path, source)
    assert _outcome(source) == (14, 17)
    assert after == (14, 17), written


def test_a_base_rebound_inside_a_branch_before_its_subclass_keeps_its_meaning(
    tmp_path: Path,
) -> None:
    """The same rebinding under an ``if`` is no more decidable for being conditional.

    Which class ``Beta`` inherits now depends on a value Towel does not
    evaluate, so neither answer is available: whether the ancestor is shared
    cannot be established, and a helper may not be placed as though it were.
    """
    source = (
        "REPLACE_BASE = True\n\n\n"
        "class Base:\n    pass\n\n\n"
        + _subclass("Alpha", "Base", 1)
        + "if REPLACE_BASE:\n    Base = object\n\n\n"
        + _subclass("Beta", "Base", 2)
        + _ENTRY_POINT
    )
    written, after = _refactored(tmp_path, source)
    assert _outcome(source) == (14, 17)
    assert after == (14, 17), written


def test_an_ordinary_base_class_still_receives_the_shared_helper_as_a_method(
    tmp_path: Path,
) -> None:
    """Nothing rebinds ``Base``, so the helper belongs in it and must land there.

    This is the refactoring the other two tests decline, with the one reason
    for declining removed. It guards the fix against the cheapest way to pass
    them, which is to stop resolving base classes at all.
    """
    source = (
        "class Base:\n    pass\n\n\n"
        + _subclass("Alpha", "Base", 1)
        + _subclass("Beta", "Base", 2)
        + _ENTRY_POINT
    )
    written, after = _refactored(tmp_path, source)
    assert _outcome(source) == (14, 17)
    assert after == (14, 17), written
    assert len(_helper_names(written, "Base")) == 1, written
    assert written.count("self._extracted_func_0(value)") == 2, written
