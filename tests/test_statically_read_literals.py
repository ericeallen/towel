"""A literal that a tool reads where it stands never becomes a helper parameter.

Message extraction (pybabel, xgettext, Django's makemessages) reads the
literal arguments of translation markers without running the program: turn
python-statemachine's ``_("There should be exactly one initial state")``
into ``_(__param_1)`` and the message leaves the catalogue, so it is never
translated again. Type checkers read the literal arguments of typing forms
the same way: ``TypeVar(__param_0)``, ``cast(__param_0, v)`` or
``Literal[__param_0]`` cannot be checked under any annotation. Blocks that
differ in such a literal are therefore not duplicates. The positions are the
ones Babel and the checkers read, verified against pybabel 2.18, mypy and
pyright; a literal elsewhere in the call, and a name in a message position,
may still become a parameter. These blocks carry no module, so a callee is
taken to be the typing form its name spells; test_typing_forms_resolved
covers forms resolved through the module's imports.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

import pytest

from tests.test_helpers import parse_block, refactor_to_fixed_point_silently, write_module
from towel.unification.static_positions import (
    DEFAULT_TRANSLATION_KEYWORDS,
    configured_translation_keywords,
)
from towel.unification.unifier import Unifier

# Each keyword's arguments, and the 1-based ones extraction reads (messages and
# contexts), as pybabel 2.18 extracts them: Babel's DEFAULT_KEYWORDS, then
# Django's makemessages keywords, then Flask-Babel's.
TRANSLATION_POSITIONS: Mapping[str, Tuple[int, FrozenSet[int]]] = {
    "_": (1, frozenset({1})),
    "gettext": (1, frozenset({1})),
    "ngettext": (3, frozenset({1, 2})),
    "ugettext": (1, frozenset({1})),
    "ungettext": (3, frozenset({1, 2})),
    "dgettext": (2, frozenset({2})),
    "dngettext": (4, frozenset({2, 3})),
    "dpgettext": (3, frozenset({2, 3})),
    "N_": (1, frozenset({1})),
    "pgettext": (2, frozenset({1, 2})),
    "npgettext": (4, frozenset({1, 2, 3})),
    "dnpgettext": (5, frozenset({2, 3, 4})),
    "gettext_lazy": (1, frozenset({1})),
    "ngettext_lazy": (3, frozenset({1, 2})),
    "pgettext_lazy": (2, frozenset({1, 2})),
    "npgettext_lazy": (4, frozenset({1, 2, 3})),
    "gettext_noop": (1, frozenset({1})),
    "ugettext_lazy": (1, frozenset({1})),
    "lazy_gettext": (1, frozenset({1})),
    "lazy_ngettext": (3, frozenset({1, 2})),
    "lazy_pgettext": (2, frozenset({1, 2})),
}


def _unifies(first: str, second: str, **options: bool) -> bool:
    return (
        Unifier(**options).unify_blocks([parse_block(first), parse_block(second)], [{}, {}])
        is not None
    )


def _call(keyword: str, arguments: Sequence[str]) -> str:
    return f"value = {keyword}({', '.join(repr(argument) for argument in arguments)})"


@pytest.mark.parametrize(
    ("keyword", "position"),
    [
        (keyword, position)
        for keyword, (count, _read) in TRANSLATION_POSITIONS.items()
        for position in range(1, count + 1)
    ],
)
def test_a_differing_literal_unifies_only_where_extraction_does_not_read_it(
    keyword: str, position: int
) -> None:
    count, read = TRANSLATION_POSITIONS[keyword]
    first = [f"{keyword} argument {index}" for index in range(1, count + 1)]
    second = list(first)
    second[position - 1] = f"another {keyword} argument {position}"
    assert _unifies(_call(keyword, first), _call(keyword, second)) is (position not in read)


@pytest.mark.parametrize(
    "callee", ["_", "self._", "translation.gettext", "i18n.lazy_pgettext", "gettext_lazy"]
)
def test_a_marker_reached_bare_or_as_an_attribute_keeps_its_literal(callee: str) -> None:
    arguments = "'menu', " if callee.endswith("pgettext") else ""
    assert not _unifies(
        f"label = {callee}({arguments}'Open')", f"label = {callee}({arguments}'Close')"
    )


def test_a_marker_is_never_replaced_by_a_parameter() -> None:
    # ``__param_0('Open')`` is a call extraction does not recognise.
    assert not _unifies("label = _('Open')", "label = gettext('Open')")
    assert not _unifies("label = _('Open')", "label = show('Open')")
    assert not _unifies("label = self._('Open')", "label = self.show('Open')")


def test_a_message_literal_is_not_replaced_by_a_name_either() -> None:
    assert not _unifies("label = _('Open')", "label = _(title)")
    assert not _unifies("label = _(prefix + 'Open')", "label = _(prefix + 'Close')")


def test_what_extraction_cannot_read_may_still_differ() -> None:
    # A name in a message position was never extracted; the domain of
    # ``dgettext`` and the count of ``ngettext`` are not messages.
    assert _unifies("label = _(title)", "label = _(heading)")
    assert _unifies("label = dgettext(domain, 'Open')", "label = dgettext(other, 'Open')")
    assert _unifies(
        "label = ngettext('file', 'files', count)", "label = ngettext('file', 'files', total)"
    )
    # A literal the marker's result is combined with is not read either.
    assert _unifies("label = _('Open') + ' now'", "label = _('Open') + ' later'")


def test_a_whole_marker_call_may_move_to_the_call_site() -> None:
    # The site then holds ``_('Open')`` itself, where extraction reads it.
    assert _unifies("label = _('Open')\nshow(label)", "label = title\nshow(label)")


def test_a_literal_promoted_from_a_factory_call_is_not_a_message() -> None:
    first = parse_block("""
        label = _('Total')
        show(label, 1)
        """)
    second = parse_block("""
        label = _('Total')
        show(label, 2)
        """)
    substitution = Unifier(promote_equal_hof_literals=True).unify_blocks([first, second], [{}, {}])
    assert substitution is not None
    promoted = {
        expression.value
        for expressions in substitution.param_expressions.values()
        for _index, expression in expressions
        if isinstance(expression, ast.Constant)
    }
    assert "Total" not in promoted


NOT_DUPLICATES: List[Tuple[str, str]] = [
    ('T = TypeVar("T")', 'U = TypeVar("U")'),
    ('T = typing.TypeVar("T")', 'U = typing.TypeVar("U")'),
    ('T = TypeVar("T", bound=Alpha)', 'T = TypeVar("T", bound=Beta)'),
    ('P = ParamSpec("P")', 'Q = ParamSpec("Q")'),
    ('Ts = TypeVarTuple("Ts")', 'Us = TypeVarTuple("Us")'),
    ('UserId = NewType("UserId", int)', 'OrderId = NewType("OrderId", int)'),
    ('UserId = NewType("UserId", Alpha)', 'UserId = NewType("UserId", Beta)'),
    ('Point = NamedTuple("Point", [("x", int)])', 'Point = NamedTuple("Point", [("y", int)])'),
    ('Point = namedtuple("Point", "x y")', 'Point = namedtuple("Point", "x z")'),
    ('Point = namedtuple("Point", ["x", "y"])', 'Point = namedtuple("Point", ["x", "z"])'),
    ('Movie = TypedDict("Movie", {"name": str})', 'Movie = TypedDict("Movie", {"title": str})'),
    ('Color = Enum("Color", "RED GREEN")', 'Color = Enum("Color", "RED BLUE")'),
    ('Color = enum.Enum("Color", "RED")', 'Shade = enum.Enum("Shade", "RED")'),
    ('Alias = TypeAliasType("Alias", int)', 'Other = TypeAliasType("Other", int)'),
    ('value = cast("int", raw)', 'value = cast("str", raw)'),
    ('value = t.cast("Alpha", raw)', 'value = t.cast("Beta", raw)'),
    ('value = typing.cast(Literal["a"], raw)', 'value = typing.cast(Literal["b"], raw)'),
    # A type written as an expression is read as surely as one in a string.
    ("value = cast(Alpha, raw)", "value = cast(Beta, raw)"),
    ("value = t.cast(List[Alpha], raw)", "value = t.cast(List[Beta], raw)"),
    ("assert_type(value, Alpha)", "assert_type(value, Beta)"),
    ('kind = Literal["a", "b"]', 'kind = Literal["a", "c"]'),
    ('assert_type(value, "Alpha")', 'assert_type(value, "Beta")'),
    # The defining call stays where the checker looks for it, assigned to its name.
    ('T = TypeVar("T")', 'T = make_variable("T")'),
    ('T = TypeVar("T")', "T = template"),
]


@pytest.mark.parametrize(("first", "second"), NOT_DUPLICATES)
def test_blocks_differing_where_a_checker_reads_are_not_duplicates(first: str, second: str) -> None:
    assert not _unifies(first, second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("value = cast(int, first)", "value = cast(int, second)"),
        ("assert_type(first, int)", "assert_type(second, int)"),
        # sqlglot's ``exp.cast(first, 'INT')`` and SQLAlchemy's ``cast(first,
        # Integer)`` take a value where typing's cast takes a type; which one
        # a call is takes its module's imports (test_typing_forms_resolved).
        (
            'T = TypeVar("T")\nregister(T, 1)',
            'T = TypeVar("T")\nregister(T, 2)',
        ),
    ],
)
def test_what_a_checker_does_not_read_may_still_differ(first: str, second: str) -> None:
    assert _unifies(first, second)


def _message_ids(source: str, keywords: Mapping[str, FrozenSet[int]]) -> List[Tuple[str, ...]]:
    """What extraction reads from ``source``: each marker call's literals at its read positions.

    A position whose argument is not a string literal reads as ``None``, and
    extraction drops such a call, as pybabel does.
    """
    found: List[Tuple[str, ...]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
        positions = keywords.get(name)
        if positions is None:
            continue
        read: List[Optional[str]] = []
        for position in sorted(positions):
            argument = node.args[position - 1] if position <= len(node.args) else None
            literal = argument.value if isinstance(argument, ast.Constant) else None
            read.append(literal if isinstance(literal, str) else None)
        if all(value is not None for value in read):
            found.append(tuple(value for value in read if value is not None))
    return sorted(found)


STATEMACHINE = """
    from gettext import gettext as _


    def check_initial(states):
        initials = [s for s in states if s.startswith("i")]
        if len(initials) != 1:
            raise ValueError(
                _("There should be exactly one initial state: {!r}").format(initials)
            )
        return initials[0]


    def check_final(states):
        finals = [s for s in states if s.startswith("f")]
        if len(finals) != 1:
            raise ValueError(
                _("There should be exactly one final state: {!r}").format(finals)
            )
        return finals[0]
    """


def test_python_statemachines_messages_stay_in_the_catalogue(tmp_path: Path) -> None:
    keywords = {"_": frozenset({1})}
    source = textwrap.dedent(STATEMACHINE)
    final, _applied = refactor_to_fixed_point_silently(write_module(tmp_path, STATEMACHINE), 3)
    assert _message_ids(final, keywords) == _message_ids(source, keywords)
    assert len(_message_ids(final, keywords)) == 2


def _labelled_totals(marker: str) -> str:
    return f"""
        def first(items):
            total = sum(items) * 2
            label = {marker}("First total")
            return label.format(total)


        def second(items):
            total = sum(items) * 2
            label = {marker}("Second total")
            return label.format(total)
        """


PROJECT_KEYWORDS: Dict[str, Tuple[str, str, str]] = {
    "setup.cfg": (
        "setup.cfg",
        "[metadata]\nname = labelled\n\n[extract_messages]\nkeywords = _l lazy_label:1\n",
        "_l",
    ),
    "babel.cfg": (
        "babel.cfg",
        "[python: **.py]\nkeywords = _t translated:1,2\n",
        "_t",
    ),
    "pyproject.toml": (
        "pyproject.toml",
        '[project]\nname = "labelled"\nversion = "0"\n\n[tool.babel]\n'
        'mappings = [{method = "python", pattern = "**.py", keywords = ["_p", "tr:1"]}]\n',
        "_p",
    ),
}


@pytest.mark.parametrize("configuration", sorted(PROJECT_KEYWORDS))
def test_a_keyword_the_project_configures_for_extraction_is_read_too(
    tmp_path: Path, configuration: str
) -> None:
    name, text, marker = PROJECT_KEYWORDS[configuration]
    (tmp_path / name).write_text(text)
    if name != "pyproject.toml":
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "labelled"\nversion = "0"\n')
    source = _labelled_totals(marker)
    final, _applied = refactor_to_fixed_point_silently(write_module(tmp_path, source), 1)
    keywords = {marker: frozenset({1})}
    assert _message_ids(final, keywords) == _message_ids(textwrap.dedent(source), keywords)
    assert len(_message_ids(final, keywords)) == 2


@pytest.mark.parametrize(
    "pyproject",
    [
        "tool = 1\n",
        '[tool]\nbabel = "python"\n',
        "[tool.babel]\nmappings = 3\n",
        "[tool.babel]\nmappings = [1, {keywords = 5}]\n",
        "[tool.babel\n",
    ],
)
def test_a_configuration_pybabel_could_not_read_configures_nothing(
    tmp_path: Path, pyproject: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(pyproject)
    (tmp_path / "setup.cfg").write_text("[extract_messages\nkeywords = _l\n")
    module = write_module(tmp_path, "value = 1\n")
    assert configured_translation_keywords([module]) == DEFAULT_TRANSLATION_KEYWORDS


def test_an_unconfigured_name_is_an_ordinary_call(tmp_path: Path) -> None:
    # Without configuration ``_l`` is no marker, and its literal may differ.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "labelled"\nversion = "0"\n')
    final, applied = refactor_to_fixed_point_silently(
        write_module(tmp_path, _labelled_totals("_l")), 1
    )
    assert applied == 1
    assert "_l(__param_0)" in final


# Each typing form as written, and with the literal or type a checker reads
# made a parameter, annotated as favourably as the parameter can be.
TYPING_FORMS: Dict[str, Tuple[str, str, str]] = {
    "TypeVar name": ('T = TypeVar("T")', "T = TypeVar(p0)", 'Literal["T"]'),
    "TypeVar bound": ('T = TypeVar("T", bound=int)', 'T = TypeVar("T", bound=p0)', "type[int]"),
    "ParamSpec name": ('P = ParamSpec("P")', "P = ParamSpec(p0)", 'Literal["P"]'),
    "TypeVarTuple name": ('Ts = TypeVarTuple("Ts")', "Ts = TypeVarTuple(p0)", 'Literal["Ts"]'),
    "NewType name": (
        'U = NewType("U", int)\n    U(5)',
        "U = NewType(p0, int)\n    U(5)",
        'Literal["U"]',
    ),
    "NewType base": (
        'U = NewType("U", int)\n    U(5)',
        'U = NewType("U", p0)\n    U(5)',
        "type[int]",
    ),
    "NamedTuple name": (
        'P = NamedTuple("P", [("x", int)])\n    P(1).x',
        'P = NamedTuple(p0, [("x", int)])\n    P(1).x',
        'Literal["P"]',
    ),
    "NamedTuple field": (
        'P = NamedTuple("P", [("x", int)])\n    P(1).x',
        'P = NamedTuple("P", [(p0, int)])\n    P(1).x',
        'Literal["x"]',
    ),
    "namedtuple fields": (
        'P = namedtuple("P", ["x", "y"])\n    P(1, 2).x',
        'P = namedtuple("P", ["x", p0])\n    P(1, 2).x',
        'Literal["y"]',
    ),
    "TypedDict key": (
        'D = TypedDict("D", {"x": int})\n    D(x=1)',
        'D = TypedDict("D", {p0: int})\n    D(x=1)',
        'Literal["x"]',
    ),
    "Enum members": (
        'E = Enum("E", "A B")\n    E.A',
        'E = Enum("E", p0)\n    E.A',
        'Literal["A B"]',
    ),
    "Literal": ('cast(Literal["a"], x)', "cast(Literal[p0], x)", 'Literal["a"]'),
    "cast string": ('cast("int", x)', "cast(p0, x)", 'Literal["int"]'),
    "assert_type string": ('assert_type(str(x), "str")', "assert_type(str(x), p0)", "type[str]"),
}

TYPING_HEADER = (
    "from collections import namedtuple\n"
    "from enum import Enum\n"
    "from typing import (\n"
    "    Literal, NamedTuple, NewType, ParamSpec, TypedDict, TypeVar, TypeVarTuple,\n"
    "    assert_type, cast,\n"
    ")\n"
)


def _typing_module(parameterized: bool) -> Tuple[str, Dict[str, range]]:
    """A module with one function per form, and the lines each function spans."""
    lines = TYPING_HEADER.splitlines()
    spans: Dict[str, range] = {}
    for index, (name, (original, changed, annotation)) in enumerate(TYPING_FORMS.items()):
        signature = f"p0: {annotation}, x: object" if parameterized else "x: object"
        body = (changed if parameterized else original).split("\n")
        start = len(lines) + 3
        lines += [
            "",
            "",
            f"def case_{index}({signature}) -> None:",
            *("    " + line.strip() for line in body),
        ]
        spans[name] = range(start, len(lines) + 1)
    return "\n".join(lines) + "\n", spans


def _checker_error_lines(tool: str, path: Path) -> Set[int]:
    """The lines ``tool`` reports errors on, once it has provably run."""
    if tool == "mypy":
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--strict",
                "--no-incremental",
                "--python-version",
                "3.12",
                str(path),
            ],
            capture_output=True,
            text=True,
            cwd=path.parent,
            timeout=300,
        )
        assert "error:" in result.stdout or "Success" in result.stdout, (
            result.stdout + result.stderr
        )
        return {
            int(match.group(1))
            for match in re.finditer(r"^[^:]+:(\d+): error:", result.stdout, re.MULTILINE)
        }
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson", "--pythonversion", "3.12", str(path)],
        capture_output=True,
        text=True,
        cwd=path.parent,
        timeout=300,
    )
    report = json.loads(result.stdout)
    return {
        diagnostic["range"]["start"]["line"] + 1
        for diagnostic in report["generalDiagnostics"]
        if diagnostic["severity"] == "error"
    }


@pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None or importlib.util.find_spec("pyright") is None,
    reason="needs mypy and pyright",
)
def test_a_checker_rejects_every_typing_form_with_a_parameter_where_it_reads(
    tmp_path: Path,
) -> None:
    """Why these positions are pinned: no annotation of the parameter makes them check.

    Each form checks as written, and with its literal or type made a
    parameter at least one of mypy and pyright rejects it: mypy the
    functional ``NamedTuple``, ``namedtuple`` and ``NewType`` forms, pyright
    ``Enum``'s members, both the rest. A checker release that accepts one
    fails this test and returns the rule for review.
    """
    original, _ = _typing_module(parameterized=False)
    (tmp_path / "original.py").write_text(original)
    for tool in ("mypy", "pyright"):
        assert _checker_error_lines(tool, tmp_path / "original.py") == set(), tool
    changed, spans = _typing_module(parameterized=True)
    (tmp_path / "changed.py").write_text(changed)
    rejected = _checker_error_lines("mypy", tmp_path / "changed.py") | _checker_error_lines(
        "pyright", tmp_path / "changed.py"
    )
    accepted = [name for name, lines in spans.items() if not rejected & set(lines)]
    assert accepted == []
