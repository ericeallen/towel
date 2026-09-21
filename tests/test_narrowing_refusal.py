"""An extraction must not separate a narrowing test from code that depends on it.

The refusal happens where the proposal is built, so it holds whether or not
type checking is on: the same code produces the same extractions either way.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import List, Optional

import pytest

from towel.unification.models import Replacement
from towel.unification.narrowing import narrowed_names, narrowing_lost_at_call_site
from towel.unification.refactor_engine import UnificationRefactorEngine

GUARDED = textwrap.dedent("""
    class Base:
        pass


    class Klass(Base):
        def __init__(self, name: str, final: bool) -> None:
            self.name = name
            self.final = final

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, Klass):
                return NotImplemented
            return self.name == other.name and self.final == other.final

        def __hash__(self) -> int:
            return hash(self.name)


    class Enum(Base):
        def __init__(self, name: str, scoped: str) -> None:
            self.name = name
            self.scoped = scoped

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, Enum):
                return NotImplemented
            return self.name == other.name and self.scoped == other.scoped

        def __hash__(self) -> int:
            return hash(self.name)
    """).lstrip()

# The same shape with the differing expressions reading a name no test narrows.
INDEPENDENT = textwrap.dedent("""
    def describe_first(value: object, label: str) -> str:
        if not isinstance(value, str):
            return "not a string"
        prefix = label.strip().upper()
        joined = prefix + ":" + value
        return joined.replace(" ", "_")


    def describe_second(value: object, label: str) -> str:
        if not isinstance(value, str):
            return "not a string"
        prefix = label.strip().lower()
        joined = prefix + ":" + value
        return joined.replace(" ", "-")
    """).lstrip()


def _proposals(tmp_path: Path, source: str, *, min_lines: int = 2) -> List[str]:
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    engine = UnificationRefactorEngine(
        min_lines=min_lines, reuse_existing_functions=False, type_oracle=None
    )
    return [proposal.description for proposal in engine.analyze_file(str(path))]


def test_a_guard_separated_from_its_use_is_refused(tmp_path: Path) -> None:
    assert _proposals(tmp_path, GUARDED) == []


def test_a_guard_whose_narrowing_nothing_left_behind_needs_is_kept(tmp_path: Path) -> None:
    """The refusal must cost only the extractions that would actually break."""
    assert _proposals(tmp_path, INDEPENDENT) != []


def _helper(source: str) -> ast.FunctionDef:
    parsed = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(parsed, ast.FunctionDef)
    return parsed


def _site(call: str) -> Replacement:
    """A replacement over that call; the refusal reads only its statement."""
    statement = ast.parse(call).body[0]
    return Replacement(line_range=(1, 1), node=statement)


def _verdict(helper: str, *calls: str) -> Optional[str]:
    return narrowing_lost_at_call_site(_helper(helper), [_site(call) for call in calls])


HELPER = """
    def _extracted(cls, get_one, other):
        if not isinstance(other, cls):
            return NotImplemented
        return get_one()
    """


def test_a_thunk_reading_the_narrowed_name_is_named_in_the_refusal() -> None:
    reason = _verdict(HELPER, "_extracted(Klass, lambda: other.final, other)")
    assert reason is not None
    assert "other" in reason and "narrow" in reason


def test_a_thunk_reading_something_else_is_no_objection() -> None:
    assert _verdict(HELPER, "_extracted(Klass, lambda: self.final, other)") is None


def test_a_thunk_that_binds_the_name_itself_is_no_objection() -> None:
    """``lambda other: ...`` reads its own parameter, not the caller's name."""
    assert _verdict(HELPER, "_extracted(Klass, lambda other: other.final, other)") is None


def test_the_same_shape_through_a_method_call_is_refused() -> None:
    helper = """
        def _extracted(self, cls, get_one, other):
            if not isinstance(other, cls):
                return NotImplemented
            return get_one()
        """
    assert _verdict(helper, "self._extracted(Klass, lambda: other.final, other)") is not None


def test_a_none_test_narrows_too() -> None:
    helper = """
        def _extracted(get_one, value):
            if value is None:
                return None
            return get_one()
        """
    assert _verdict(helper, "_extracted(lambda: value.name, value)") is not None


@pytest.mark.parametrize(
    "test, narrowed",
    [
        ("isinstance(other, Klass)", {"other"}),
        ("not isinstance(other, Klass)", {"other"}),
        ("issubclass(kind, Base)", {"kind"}),
        ("hasattr(thing, 'name')", {"thing"}),
        ("value is None", {"value"}),
        ("value is not None", {"value"}),
        ("len(items) > 0", set()),
        ("other.field == 1", set()),
    ],
)
def test_what_counts_as_narrowing(test: str, narrowed: set[str]) -> None:
    body = ast.parse(f"if {test}:\n    pass\n").body
    assert narrowed_names(body) == narrowed


def test_a_helper_with_no_test_at_all_is_never_objected_to() -> None:
    assert (
        _verdict("def _extracted(get_one, other):\n    return get_one()\n", "_extracted(x, y)")
        is None
    )


BOUND = """
    def _extracted(self, cls, get_one, other):
        if not isinstance(other, cls):
            return NotImplemented
        return get_one()
    """


@pytest.mark.parametrize(
    "call",
    [
        "self._extracted(Klass, lambda: other.final, other)",
        "self._extracted(Klass, lambda: other.final, other=other)",
        "self._extracted(Klass, other=other, get_one=lambda: other.final)",
    ],
    ids=["positional", "mixed", "keywords"],
)
def test_however_the_call_is_written_the_narrowed_name_is_found(call: str) -> None:
    """A receiver is bound by the call's form, which is one fewer argument than
    parameters however the rest are passed; counting only positions mislaid it."""
    assert _verdict(BOUND, call) is not None, call


def test_a_free_function_call_maps_its_arguments_from_the_first() -> None:
    free = """
        def _extracted(cls, get_one, other):
            if not isinstance(other, cls):
                return NotImplemented
            return get_one()
        """
    assert _verdict(free, "_extracted(Klass, lambda: other.final, other)") is not None
    assert _verdict(free, "_extracted(Klass, lambda: self.final, other)") is None


def test_a_name_a_nested_scope_binds_is_hidden_only_inside_it() -> None:
    """``(other.final, lambda other: other)`` reads the caller's ``other`` once."""
    assert _verdict(BOUND, "self._extracted(K, (other.final, lambda other: other), other)")
    assert _verdict(BOUND, "self._extracted(K, lambda *other: other, other)") is None
