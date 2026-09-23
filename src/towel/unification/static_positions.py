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

"""Places where tools read a program without running it.

Message extraction (pybabel, GNU xgettext, Django's makemessages) reads the
literal arguments of translation markers from the source text: a helper that
takes the message as ``__param_1`` and calls ``_(__param_1)`` leaves the
catalogue without it, and the message is never translated again. Type
checkers read typing forms the same way: ``TypeVar(__param_0)``,
``NamedTuple(__param_0, ...)``, ``cast(__param_0, value)`` in place of
``cast("int", value)``, and ``Literal[__param_0]`` are rejected by mypy or
pyright however the parameter is annotated. So a sub-expression a tool reads where it stands may
not become a parameter, and blocks that differ in one are not duplicates.

``statically_read`` answers, per statement, which nodes are read that way
and how strictly (``Pin``). Markers are recognized by the callee's name,
bare or as the last part of a dotted callee, which is also how pybabel finds
them. Their read arguments are counted as pybabel counts them: positional
arguments, then keyword arguments, in order; a starred or ``**`` argument
makes the count unknowable, and then every argument is read. The keywords
are Babel's defaults, Django's and Flask-Babel's, plus any the project
configures for pybabel (``configured_translation_keywords``).
"""

from __future__ import annotations

import ast
import configparser
from dataclasses import dataclass, field
import enum
import os
from pathlib import Path
import tomllib
from types import MappingProxyType
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from weakref import WeakKeyDictionary


class Pin(enum.Enum):
    """How much of a node a tool reads where it stands."""

    LITERALS = "literals"
    """A string literal in it is read, as a message is: nothing holding one may be a parameter."""

    WHOLE = "whole"
    """It is read as written, as a type is: no part of it may be a parameter."""


@dataclass(frozen=True)
class TranslationKeywords:
    """Translation markers by name, each with the 1-based arguments extraction reads.

    The positions include a context argument (``pgettext``'s first), which is
    read as a message is. Hashable, so what a statement pins can be memoized
    per configuration.
    """

    read: FrozenSet[Tuple[str, FrozenSet[int]]]
    _by_name: Mapping[str, FrozenSet[int]] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        by_name: Dict[str, FrozenSet[int]] = {}
        for name, positions in self.read:
            by_name[name] = by_name.get(name, frozenset()) | positions
        object.__setattr__(self, "_by_name", MappingProxyType(by_name))

    @classmethod
    def of(cls, positions: Mapping[str, Iterable[int]]) -> "TranslationKeywords":
        return cls(frozenset((name, frozenset(read)) for name, read in positions.items()))

    def positions(self, name: str) -> Optional[FrozenSet[int]]:
        """The arguments extraction reads from a call of ``name``; None when it is no marker."""
        return self._by_name.get(name)

    def with_specs(self, specs: Iterable[str]) -> "TranslationKeywords":
        """These keywords and the ones ``specs`` names, spelled as pybabel's ``--keyword`` takes them."""
        added = [_parse_spec(spec) for spec in specs]
        return TranslationKeywords(self.read | frozenset(entry for entry in added if entry))


_EVERY_ARGUMENT = frozenset(range(1, 33))
"""The positions of a keyword whose spec cannot be read: all of them, as far as any call goes."""


def _parse_spec(spec: str) -> Optional[Tuple[str, FrozenSet[int]]]:
    """``name``, ``name:1,2``, ``name:1c,2`` or ``name:2,2t`` as the name and the positions read.

    A ``t`` part only restricts the calls a spec applies to by their number
    of arguments, so it adds no position. A spec pybabel could not parse
    either still names a marker; every argument of it is then taken to be
    read, which can only decline more.
    """
    name, _, positions = spec.strip().partition(":")
    if not name:
        return None
    if not positions:
        return (name, frozenset({1}))
    read = set()
    for part in positions.split(","):
        part = part.strip()
        if part.endswith("t") and part[:-1].isdigit():
            continue
        digits = part[:-1] if part.endswith("c") else part
        if not digits.isdigit():
            return (name, _EVERY_ARGUMENT)
        read.add(int(digits))
    return (name, frozenset(read) or frozenset({1}))


DEFAULT_TRANSLATION_KEYWORDS = TranslationKeywords.of(
    {
        # Babel's DEFAULT_KEYWORDS (babel.messages.extract, 2.18), which hold
        # GNU xgettext's Python keywords too.
        "_": {1},
        "gettext": {1},
        "ngettext": {1, 2},
        "ugettext": {1},
        "ungettext": {1, 2},
        "dgettext": {2},
        "dngettext": {2, 3},
        "dpgettext": {2, 3},
        "N_": {1},
        "pgettext": {1, 2},
        "npgettext": {1, 2, 3},
        "dnpgettext": {2, 3, 4},
        # Django's makemessages, and the spellings Django 4 removed.
        "gettext_noop": {1},
        "gettext_lazy": {1},
        "ngettext_lazy": {1, 2},
        "pgettext_lazy": {1, 2},
        "npgettext_lazy": {1, 2, 3},
        "ugettext_noop": {1},
        "ugettext_lazy": {1},
        "ungettext_lazy": {1, 2},
        # Flask-Babel's lazy forms, which its documentation adds with -k.
        "lazy_gettext": {1},
        "lazy_ngettext": {1, 2},
        "lazy_pgettext": {1, 2},
        "lazy_npgettext": {1, 2, 3},
    }
)

_TYPE_DEFINITIONS = frozenset(
    {
        "TypeVar",
        "ParamSpec",
        "TypeVarTuple",
        "NewType",
        "NamedTuple",
        "namedtuple",
        "TypedDict",
        "TypeAliasType",
        "Enum",
        "IntEnum",
        "StrEnum",
        "Flag",
        "IntFlag",
    }
)
"""Calls a checker reads whole: the name, fields and types of what they define.

The call itself stays in place too. ``T = TypeVar("T")`` defines a type
variable only as the value of an assignment to its own name, so passing the
call in from the site would leave the helper assigning a parameter.
"""

_TYPE_ARGUMENTS = {"cast": (1, "typ"), "assert_type": (2, "typ")}
"""Calls one argument of which a checker reads as a type, by position and by keyword.

Only a type spelled as a string is pinned. Other libraries reuse the name
with a value in that place (sqlglot's ``exp.cast(column, to)``, SQLAlchemy's
``cast(column, Integer)``), and telling a type from a value there takes the
module's imports, which a block does not carry; a type written as an
expression is left to the project's checker.
"""

_LITERAL_TYPES = frozenset({"Literal"})
"""Subscripted forms whose subscript a checker reads as values."""


def _callee_name(callee: ast.expr) -> Optional[str]:
    """The name a call is recognized by: a bare name, or the last part of a dotted one."""
    if isinstance(callee, ast.Name):
        return callee.id
    if isinstance(callee, ast.Attribute):
        return callee.attr
    return None


def _arguments_at(call: ast.Call, positions: FrozenSet[int]) -> Iterator[ast.expr]:
    """The arguments at these 1-based positions, positional ones first, then keywords, in order."""
    counted: List[ast.expr] = [*call.args, *(keyword.value for keyword in call.keywords)]
    if any(isinstance(argument, ast.Starred) for argument in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    ):
        yield from counted
        return
    for position, argument in enumerate(counted, start=1):
        if position in positions:
            yield argument


def _type_argument(call: ast.Call, position: int, keyword: str) -> Iterator[ast.expr]:
    if len(call.args) >= position:
        yield call.args[position - 1]
    for passed in call.keywords:
        if passed.arg in {keyword, None}:
            yield passed.value


def _compute_pins(statement: ast.AST, keywords: TranslationKeywords) -> Mapping[int, Pin]:
    pins: Dict[int, Pin] = {}

    def pin(node: ast.AST, how: Pin) -> None:
        for inner in ast.walk(node):
            if pins.get(id(inner)) is not Pin.WHOLE:
                pins[id(inner)] = how

    for node in ast.walk(statement):
        if isinstance(node, ast.Call):
            name = _callee_name(node.func)
            if name is None:
                continue
            read = keywords.positions(name)
            if read is not None:
                # The name is what extraction recognizes; the receiver of
                # ``self._`` is not read and may still differ.
                pins[id(node.func)] = Pin.WHOLE
                for argument in _arguments_at(node, read):
                    pin(argument, Pin.LITERALS)
            elif name in _TYPE_DEFINITIONS:
                pin(node, Pin.WHOLE)
            elif name in _TYPE_ARGUMENTS:
                for argument in _type_argument(node, *_TYPE_ARGUMENTS[name]):
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        pins[id(node.func)] = Pin.WHOLE
                        pin(argument, Pin.WHOLE)
        elif isinstance(node, ast.Subscript) and _callee_name(node.value) in _LITERAL_TYPES:
            pins[id(node.value)] = Pin.WHOLE
            pin(node.slice, Pin.WHOLE)
    return MappingProxyType(pins)


_PINS: "WeakKeyDictionary[ast.AST, Tuple[TranslationKeywords, Mapping[int, Pin]]]" = (
    WeakKeyDictionary()
)


def statically_read(statement: ast.AST, keywords: TranslationKeywords) -> Mapping[int, Pin]:
    """The nodes of ``statement`` a tool reads where they stand, by ``id``, with how strictly.

    Memoized per statement for one configuration of keywords: every
    unification of a block asks this of each of its statements.
    """
    cached = _PINS.get(statement)
    if cached is not None and cached[0] == keywords:
        return cached[1]
    pins = _compute_pins(statement, keywords)
    try:
        _PINS[statement] = (keywords, pins)
    except TypeError:  # a node type that cannot be weakly referenced
        pass
    return pins


def holds_string_literal(expression: ast.AST) -> bool:
    """Whether a string or bytes literal occurs anywhere in ``expression``."""
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))
        for node in ast.walk(expression)
    )


def is_statically_read(expression: ast.AST, pins: Mapping[int, Pin]) -> bool:
    """Whether ``expression`` must stay where it stands for a tool to read it."""
    how = pins.get(id(expression))
    if how is Pin.WHOLE:
        return True
    return how is Pin.LITERALS and holds_string_literal(expression)


_PROJECT_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py")
_MAPPING_FILES = ("babel.cfg", "babel.ini")


def _ini_keywords(path: Path, sections: Optional[Sequence[str]] = None) -> List[str]:
    """The ``keywords`` of an INI file's sections (all but ``[extractors]``, or those named)."""
    parser = configparser.RawConfigParser()
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle, str(path))
    except (OSError, UnicodeError, configparser.Error):
        return []
    specs: List[str] = []
    for section in parser.sections():
        if section == "extractors" or (sections is not None and section not in sections):
            continue
        specs.extend(parser.get(section, "keywords", fallback="").split())
    return specs


def _mapping_file(setup_cfg: Path) -> Optional[Path]:
    """The mapping file ``setup.cfg`` names for ``extract_messages``, if any."""
    parser = configparser.RawConfigParser()
    try:
        with open(setup_cfg, encoding="utf-8") as handle:
            parser.read_file(handle, str(setup_cfg))
    except (OSError, UnicodeError, configparser.Error):
        return None
    for option in ("mapping_file", "mapping-file"):
        named = parser.get("extract_messages", option, fallback=None)
        if named:
            return setup_cfg.parent / named.strip()
    return None


def _toml_keywords(path: Path, *, in_pyproject: bool) -> List[str]:
    """The ``keywords`` of a TOML mapping: ``[tool.babel]`` in pyproject.toml, or top level."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    babel = data.get("tool", {}).get("babel", {}) if in_pyproject else data
    mappings = babel.get("mappings", []) if isinstance(babel, dict) else []
    specs: List[str] = []
    for mapping in mappings if isinstance(mappings, list) else []:
        keywords = mapping.get("keywords") if isinstance(mapping, dict) else None
        if isinstance(keywords, str):
            specs.extend(keywords.split())
        elif isinstance(keywords, list):
            specs.extend(keyword for keyword in keywords if isinstance(keyword, str))
    return specs


def _directory_keywords(directory: Path) -> List[str]:
    """The extraction keywords the pybabel configuration in ``directory`` adds."""
    specs: List[str] = []
    setup_cfg = directory / "setup.cfg"
    if setup_cfg.is_file():
        specs += _ini_keywords(setup_cfg, sections=("extract_messages",))
        named = _mapping_file(setup_cfg)
        if named is not None and named.is_file():
            specs += _ini_keywords(named)
    for name in _MAPPING_FILES:
        if (directory / name).is_file():
            specs += _ini_keywords(directory / name)
    if (directory / "babel.toml").is_file():
        specs += _toml_keywords(directory / "babel.toml", in_pyproject=False)
    if (directory / "pyproject.toml").is_file():
        specs += _toml_keywords(directory / "pyproject.toml", in_pyproject=True)
    return specs


def _specs_up_to_root(directory: str, read: Dict[str, Tuple[str, ...]]) -> Tuple[str, ...]:
    """The specs configured in ``directory`` and each parent, through the nearest project root.

    pybabel reads its configuration from the project root, where
    ``pyproject.toml``, ``setup.cfg`` and a mapping file conventionally sit;
    a directory in between that holds one is read too, which can only
    decline more. ``read`` holds the directories already read.
    """
    cached = read.get(directory)
    if cached is not None:
        return cached
    path = Path(directory)
    specs = tuple(_directory_keywords(path))
    if not any((path / marker).is_file() for marker in _PROJECT_MARKERS):
        parent = os.path.dirname(directory)
        if parent != directory:
            specs += _specs_up_to_root(parent, read)
    read[directory] = specs
    return specs


def configured_translation_keywords(paths: Iterable[str]) -> TranslationKeywords:
    """The default keywords and those the projects holding ``paths`` configure for pybabel.

    Read from ``setup.cfg``'s ``[extract_messages]`` (and the mapping file it
    names), from ``babel.cfg``, ``babel.ini`` or ``babel.toml``, and from
    ``[tool.babel]`` in ``pyproject.toml``. Keywords given only on a command
    line cannot be read. A file that does not parse configures nothing, as it
    could not configure pybabel either.
    """
    read: Dict[str, Tuple[str, ...]] = {}
    directories = sorted({os.path.dirname(os.path.abspath(path)) for path in paths})
    specs = [spec for directory in directories for spec in _specs_up_to_root(directory, read)]
    return DEFAULT_TRANSLATION_KEYWORDS.with_specs(specs) if specs else DEFAULT_TRANSLATION_KEYWORDS
