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

"""The comments of a moved block, and where they stand in its helper.

A helper is rendered from its syntax tree, and a syntax tree holds no
comments, so each comment of a duplicate is read from the source tokens
(``site_comments``) and recorded against the code it is written beside: the
node it follows or precedes, named by a path of typed steps from the
block's statements (``NodePath``), the punctuation between them, and the
brackets around it. The helper's body is the first site's block with
parameters substituted, so a path names the same code in the helper, down
to the node a parameter took the place of.

Rendering (``weave_comments``) writes each comment into the unparsed helper
at that place, before the project's formatter runs: at the end of the line
holding its code, inside the brackets it stood in, or on a line of its own
before or after its statement. Where the source had them, the grouping
parentheses around the comment and the trailing comma that kept a bracket
exploded are written too, so Black and ruff lay the code out around the
comment as the source had it. After formatting, every tool directive must
still stand on the line of the code it was written beside; a formatter that
moved one is not used for that helper (``WovenHelper.keeps_directives``).

Which comments the helper carries is decided from every site's
(``merge_comments``). An explanatory comment is kept wherever any site
carries it: it documents code the helper now holds, and one site's words
are not lost for another's silence. A tool directive (``is_directive``)
changes what a tool reports for its line, and the helper has one line where
the sites had several, so the sites must agree: every site must carry the
same directives at the same places (``DIRECTIVES_DIFFER`` otherwise), no
directive may reach code of a site's that becomes an argument of the call,
written at the call site where the directive does not reach
(``DIRECTIVE_ON_ARGUMENT``), no ignore above a block may govern its first
statement, which the call takes the place of, nor any region directive
opened before it reach it unless it also reaches the helper
(``DIRECTIVE_AROUND_BLOCK``), a directive whose reach is a region or a
file must not reach past the moved code (``DIRECTIVE_OUTLIVES_BLOCK``), and
no directive may stand on a line the block shares with code that stays at
the call site (``a = 1; b = 2  # noqa`` with the block at ``b``): the
directive governs the whole line, and splicing the call in parts it from
some of that code (``DIRECTIVE_ON_SHARED_LINE``).
"""

from __future__ import annotations

import ast
import bisect
import functools
import io
import re
import tokenize
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

from ..canonical_ast import canonical_dump
from .exceptions import RefactoringError
from .substitution import Substitution
from ..coverage_config import DEFAULT_EXCLUDE, CoverageExclusion
from ..source_text import source_lines
from .splicing import shared_lines

DEFAULT_EXCLUSION = CoverageExclusion().pattern
"""coverage.py's own exclusion regexes, joined: for a block whose project sets none."""

Position = Tuple[int, int]
"""A (line, column) place in a text; the line counts from 1 and the column in characters."""

Step = Tuple[str, int, str]
"""One step down a tree: the field, the index in it (-1 for a single node), and the node type reached."""


@dataclass(frozen=True)
class NodePath:
    """A node of a block: the statement it is in, and the typed steps down to it."""

    statement: int
    kind: str
    steps: Tuple[Step, ...] = ()

    def child(self, field_name: str, index: int, kind: str) -> NodePath:
        return NodePath(self.statement, self.kind, self.steps + ((field_name, index, kind),))

    def prefix(self, length: int) -> NodePath:
        return NodePath(self.statement, self.kind, self.steps[:length])


class Placement(Enum):
    """How a comment stands beside the node of its anchor."""

    TRAILING = "trailing"
    """At the end of the line, after the node and the punctuation that followed it."""
    FIRST_LINE = "first_line"
    """At the end of the statement's first line: its line held nothing of the block before it."""
    HEADER = "header"
    """At the end of the ``else:``, ``try:``, ``finally:`` or ``except:`` line opening the node's clause."""
    LEADING = "leading"
    """On a line of its own, before the node."""
    CLOSING = "closing"
    """On a line of its own, after the node and its punctuation."""
    ABOVE_HEADER = "above_header"
    """On a line of its own, before the header line of the node's clause."""


_OWN_LINE = frozenset({Placement.LEADING, Placement.CLOSING, Placement.ABOVE_HEADER})


@dataclass(frozen=True)
class Anchor:
    """Where a comment stands in its block's code."""

    node: NodePath
    placement: Placement
    punctuation: Tuple[str, ...] = ()
    # The expression that the innermost parentheses around the comment
    # enclosed, when they were grouping parentheses, which unparsing drops.
    group: Optional[NodePath] = None
    # The call, list, tuple, set or dict whose brackets enclosed the comment,
    # when a trailing comma held them exploded, which unparsing drops too.
    exploded: Optional[NodePath] = None

    @property
    def own_line(self) -> bool:
        return self.placement in _OWN_LINE


@dataclass(frozen=True)
class BlockComment:
    """One comment of a duplicate block, as written, with its anchor and its line there."""

    text: str
    anchor: Anchor
    line: int
    # For a comment at the end of a line, the first node that line started:
    # with the anchor, the span of code the comment was written beside.
    line_start: Optional[NodePath] = None
    # The lines of the block whose code a tool directive governs: its own
    # line, the statement or clause it disables, the region it opens, or the
    # statement it precedes; none for a comment that tells no tool anything,
    # or one that configures the whole file. What coverage excludes is
    # ``SiteComments.excluded``.
    reach: FrozenSet[int] = frozenset()
    # Whether a tool reads the comment (``_counts_as_directive``): a pragma
    # the project's coverage.py does not exclude by is a plain comment.
    directive: bool = False
    # The column the comment starts at, which a region's closer must share.
    column: int = 0


@dataclass(frozen=True)
class SiteComments:
    """The comments of one call site's block, and the lines where its code becomes an argument."""

    comments: Tuple[BlockComment, ...] = ()
    # Lines holding an expression of this block that its call passes as an
    # argument (or inside a lambda) and that is neither a name nor a literal.
    argument_lines: FrozenSet[int] = frozenset()
    # The block's tokens could not be read, so neither could its comments.
    unreadable: bool = False
    # The directives outside the block that reach it (``_around``), which
    # a helper holding it may be out of reach of (``Surrounding``).
    around: Tuple["Surrounding", ...] = ()
    # The lines of the block coverage.py excludes, by a regex of the
    # project's configuration or its defaults, with each whole statement and
    # clause an excluded line opens (``_excluded``).
    excluded: FrozenSet[int] = frozenset()
    # Why the block's first statement is excluded from coverage, which the
    # call that replaces the block would not be (``_excluded_start``); or empty.
    excluded_start: str = ""
    # The ignore on a line of its own above the block that governs its first
    # statement, and would govern the call instead (``_directive_before``).
    directed_start: str = ""
    # The directive on a line the block shares with code outside it, or the
    # coverage exclusion of such a line (``_shared_line_directive``); or empty.
    shared_line_directive: str = ""
    # The exclusion of code outside the block by a match of the coverage
    # regexes that reaches into it, which moving it would end
    # (``_exclusion_moves``); or empty.
    excluded_across: str = ""


Home = Tuple[str, str]
"""Where a helper may be written: ``("module", "")``, ``("function", name)``, or a class.

A class is ``("class", name)`` for a helper written after the method holding
the block, as a method helper is, and ``("whole-class", name)`` for one that
may be written anywhere in the class body.
"""


@dataclass(frozen=True)
class Surrounding:
    """A directive outside a block that reaches it, and the helpers it would still reach.

    ``tool`` is ``"coverage"`` for an exclusion, or the family of a region
    directive (``_REGION_FAMILIES``), a ``pylint: disable`` ending an
    enclosing header included. ``reaches`` names the homes whose helper it
    reaches too: a directive ending the header of the class or function it
    governs reaches a helper written inside it; a region reaches a module
    helper when it opens before the module's first definition and is never
    closed, a class's when it covers the class to its end from before the
    method holding the block (or from before the class's first statement,
    for the whole class), and a nested helper when it opens before the
    function's first statement.
    """

    detail: str
    tool: str = "coverage"
    reaches: FrozenSet[Home] = frozenset()
    # Whether the directive keeps a formatter off the block's layout.
    layout: bool = False

    def governs(self, home_class: Optional[str], home_function: Optional[str]) -> bool:
        """Whether the directive still reaches a helper placed in ``home_class`` or ``home_function``.

        A directive that keeps a formatter off the code must reach the
        helper wherever in its class it is written.
        """
        if home_class is not None:
            return ("whole-class" if self.layout else "class", home_class) in self.reaches
        if home_function is not None:
            return ("function", home_function) in self.reaches
        return ("module", "") in self.reaches


@dataclass(frozen=True)
class HelperComment:
    """A comment the helper carries, anchored in the helper's body."""

    text: str
    anchor: Anchor
    directive: bool = False
    line_start: Optional[NodePath] = None


@dataclass(frozen=True)
class HelperComments:
    """The comments a helper carries; paths count statements from ``body_offset``."""

    body_offset: int = 0
    comments: Tuple[HelperComment, ...] = ()


class ConflictKind(Enum):
    """Why the sites' comments cannot all move into one helper."""

    DIRECTIVES_DIFFER = "directives_differ"
    DIRECTIVE_ON_ARGUMENT = "directive_on_argument"
    DIRECTIVE_OUTLIVES_BLOCK = "directive_outlives_block"
    DIRECTIVE_AROUND_BLOCK = "directive_around_block"
    EXCLUDED_BLOCK_START = "excluded_block_start"
    DIRECTIVE_ON_SHARED_LINE = "directive_on_shared_line"


@dataclass(frozen=True)
class CommentConflict:
    """A reason the sites' comments decline the pair, with the comment it concerns."""

    kind: ConflictKind
    detail: str


class CommentPlacementError(RefactoringError):
    """The helper's comments could not be written into its rendered text."""


# -- Directives ----------------------------------------------------------------

_DIRECTIVE = re.compile(
    r"""\#\s*(?:
        type\s*:\s*\S                       # PEP 484 type comments and ignores
      | pyright\s*:\s*\S | mypy\s*:\s*\S | pytype\s*:\s*\S   # pyright and basedpyright alike
      | pyre-(?:ignore|fixme|strict|unsafe|ignore-all-errors)\b
      | ty\s*:\s*ignore\b | pyrefly\s*:\s*ignore\b | zuban\s*:\s*ignore\b
      | noqa\b | flake8[:=\s]\s*noqa\b | ruff\s*:\s*\S
      | pragma\b | nosec\b | pylint\s*:\s*\S | noinspection\b
      | nosemgrep\b | lint-(?:fixme|ignore)\b  # Semgrep, Fixit
      | fmt\s*:\s*(?:off|on|skip)\b | yapf\s*:\s*(?:disable|enable)\b
      | isort\s*:\s*\S | autopep8\s*:\s*(?:off|on)\b
      | pycln\s*:\s*\S | nopycln\b | codespell\s*:\s*ignore\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# A directive that pylint applies to the whole statement holding its line,
# and to the whole block when that line opens one, as coverage.py does with
# an excluded line (``_excluded``).
_STATEMENT_WIDE = re.compile(r"\#\s*pylint\s*:\s*disable\b", re.IGNORECASE)

# coverage.py's default exclusion pragma, which excludes nothing in a project
# whose ``exclude_lines`` leaves it out.
_DEFAULT_PRAGMA = re.compile(DEFAULT_EXCLUDE[0])

# A directive on a line of its own that governs the statement after it:
# PyCharm's ``noinspection``, and isort's code-sorting comments, which sort the
# literal or the assignments that follow (``isort/core.py``,
# ``CODE_SORT_COMMENTS``, in isort 9.0.1).
_NEXT_STATEMENT = re.compile(
    r"\#\s*(?:noinspection\b"
    r"|isort\s*:\s*(?:list|dict|set|unique-list|tuple|unique-tuple|assignments)\b)",
    re.IGNORECASE,
)

# An ignore on a line of its own that governs the next line of code: ty's and
# ruff's the next logical line, or inside brackets the next physical line;
# pyre's, pyrefly's, Semgrep's, Fixit's and pylint's ``disable-next`` the next
# line. pyrefly reads every checker's ``<tool>: ignore`` that way, ``type:
# ignore`` included, so a project it checks has them all reach the next line.
_NEXT_LINE = re.compile(
    r"""\#\s*(?:
        (?:type|ty|pyrefly|pyre|pyright|mypy|zuban|ruff)\s*:\s*ignore\b(?!-)
      | pyre-(?:ignore|fixme)\b(?!-) | nosemgrep\b | lint-(?:fixme|ignore)\b
      | pylint\s*:\s*disable-next\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_FILE_DIRECTIVE = re.compile(
    r"\#\s*(?:flake8[:=\s]\s*noqa\b|ruff\s*:\s*(?:noqa|file-ignore)\b|mypy\s*:"
    r"|pyright\s*:\s*(?!ignore\b)\w|pyrefly\s*:\s*ignore-errors\b"
    r"|isort\s*:\s*skip_file\b|pylint\s*:\s*skip-file\b|pytype\s*:\s*skip-file\b"
    r"|pyre-(?:strict|unsafe|ignore-all-errors)\b)",
    re.IGNORECASE,
)

# -- Regions -------------------------------------------------------------------
#
# A region directive on a line of its own opens a region its tool applies to
# every line up to the directive that closes it. Every tool whose directives
# ``_DIRECTIVE`` reads was checked for one: flake8's ``noqa``, mypy, pyright,
# ty, pyrefly, zuban, pyre, Bandit's ``nosec``, Semgrep, Fixit, pycln and
# codespell govern a line, the next line or the file, and have none; the
# families below are the rest.
#
# An opener is recognized broadly, and a closer as its tool is known to read
# it: a comment a tool may take for a closer when it does not would end the
# region early, and let code move out of it unnoticed. Where a tool's reading
# was not verified, only the spellings any reading accepts close a region.


class _Scope(Enum):
    """How far a tool carries a region: what closes it, and where it ends unclosed."""

    BLOCK = "block"
    """A closer at the opener's column in the same body; unclosed, the end of that body."""
    FILE = "file"
    """A closer anywhere after it, line by line; unclosed, the end of the file."""
    EITHER = "either"
    """Not verified against the tool: wherever either reading reaches, and closed only as both are."""


def _exactly(*spellings: str) -> Callable[[str, str], bool]:
    """A closing test accepting only ``spellings`` of a closer, whatever the opener."""
    accepted = frozenset(spellings)
    return lambda opener, closer: closer.strip() in accepted


_NAMED = re.compile(r"\b(disable|enable)\s*=\s*([^#]*)", re.IGNORECASE)


def _named(text: str, verb: str) -> Optional[FrozenSet[str]]:
    """The messages a pylint or pytype ``disable=``/``enable=`` names; None when it names none."""
    match = _NAMED.search(text)
    if match is None or match.group(1).lower() != verb:
        return None
    names = frozenset(name.strip() for name in match.group(2).split(",") if name.strip())
    return names or None


def _enables_what_it_disabled(opener: str, closer: str) -> bool:
    """pylint's and pytype's rule: an ``enable`` ends what it names, so it must name all of it.

    ``disable-next`` governs one line and is not closed by anything.
    """
    if re.search(r"disable-next\b", opener, re.IGNORECASE):
        return False
    disabled, enabled = _named(opener, "disable"), _named(closer, "enable")
    return (
        disabled is not None and enabled is not None and ("all" in enabled or disabled <= enabled)
    )


# ruff 0.16's range suppression, ``# ruff: disable[E501]`` ... ``# ruff:
# enable[E501]``. Measured on ruff 0.16.9: ``ruff`` is read in lower case
# only, spacing anywhere is ignored, the codes must be the same list in the
# same order, a closer without codes or at another indentation is invalid
# (RUF103), and a region left unclosed (RUF104) covers the rest of the body
# it opens in, as does a trailing ``disable``, which ruff rejects.
_RUFF_RANGE = re.compile(r"#\s*ruff\s*:\s*(disable|enable)\s*\[([^\]]*)\]")


def _ruff_codes(text: str, verb: str) -> Optional[Tuple[str, ...]]:
    match = _RUFF_RANGE.search(text)
    if match is None or match.group(1) != verb:
        return None
    codes = tuple(code.strip() for code in match.group(2).split(",") if code.strip())
    return codes or None


def _ruff_closes(opener: str, closer: str) -> bool:
    opened = _ruff_codes(opener, "disable")
    return opened is not None and opened == _ruff_codes(closer, "enable")


@dataclass(frozen=True)
class _Region:
    """A family of region directives: the tool reading them, and how it reads them."""

    family: str
    opens: re.Pattern[str]
    # Anything its tool might read as a closer; ``closed_by`` says whether it does.
    closes: re.Pattern[str]
    scope: _Scope
    # Whether it keeps a formatter off the code's layout, which then must not
    # be lost at any site; otherwise it changes what a linter reports, which a
    # site outside the region already reports for the same code.
    layout: bool
    closed_by: Callable[[str, str], bool]


def _pattern(*alternatives: str) -> re.Pattern[str]:
    return re.compile("|".join(rf"\#\s*{alternative}\b" for alternative in alternatives), re.I)


_FMT_OFF, _FMT_ON = r"fmt\s*:\s*off", r"fmt\s*:\s*on"
_YAPF_OFF, _YAPF_ON = r"yapf\s*:\s*disable", r"yapf\s*:\s*enable"

_REGION_FAMILIES: Tuple[_Region, ...] = (
    # Black (``black/comments.py``, FMT_OFF and FMT_ON, 26.5.1) and ruff format
    # (measured on 0.16.9): a closer at the opener's level, ``# FMT: ON`` not
    # one; ``# yapf: disable`` and ``# yapf: enable`` count as ``fmt: off``
    # and ``fmt: on``.
    _Region(
        "black",
        _pattern(_FMT_OFF, _YAPF_OFF),
        _pattern(_FMT_ON, _YAPF_ON),
        _Scope.BLOCK,
        True,
        _exactly("# fmt: on", "# fmt:on", "# yapf: enable"),
    ),
    # yapf reads its own and Black's spellings, line by line; not verified.
    _Region(
        "yapf",
        _pattern(_YAPF_OFF, _FMT_OFF),
        _pattern(_YAPF_ON, _FMT_ON),
        _Scope.EITHER,
        True,
        _exactly("# yapf: enable", "# fmt: on"),
    ),
    # autopep8 reads its own and Black's spellings, to the end of the file
    # when not closed; not verified.
    _Region(
        "autopep8",
        _pattern(r"autopep8\s*:\s*off", _FMT_OFF),
        _pattern(r"autopep8\s*:\s*on", _FMT_ON),
        _Scope.EITHER,
        True,
        _exactly("# autopep8: on", "# fmt: on"),
    ),
    # isort 9.0.1 (``isort/core.py``): a whole line reading exactly ``# isort:
    # off`` or ``# isort: on``, line by line whatever the indentation.
    _Region(
        "isort",
        _pattern(r"isort\s*:\s*off"),
        _pattern(r"isort\s*:\s*on"),
        _Scope.FILE,
        False,
        _exactly("# isort: on"),
    ),
    # pylint: to the end of the statement the pragma stands in, or an
    # ``enable`` of every message it named.
    _Region(
        "pylint",
        _pattern(r"pylint\s*:\s*disable"),
        _pattern(r"pylint\s*:\s*enable"),
        _Scope.BLOCK,
        False,
        _enables_what_it_disabled,
    ),
    _Region(
        "ruff",
        _pattern(r"ruff\s*:\s*disable"),
        _pattern(r"ruff\s*:\s*enable"),
        _Scope.BLOCK,
        False,
        _ruff_closes,
    ),
    # pytype's ``disable`` on a line of its own; its reach was not verified.
    _Region(
        "pytype",
        _pattern(r"pytype\s*:\s*disable"),
        _pattern(r"pytype\s*:\s*enable"),
        _Scope.EITHER,
        False,
        _enables_what_it_disabled,
    ),
)

# Any comment that may open or close a region, for a quick test of a module.
_ANY_REGION = re.compile(
    "|".join(
        f"(?:{pattern.pattern})"
        for family in _REGION_FAMILIES
        for pattern in (family.opens, family.closes)
    ),
    re.IGNORECASE,
)


def is_directive(text: str) -> bool:
    """Whether the comment ``text`` tells a tool something: an ignore, a pragma, a type.

    Type checkers (``type: ignore``, a PEP 484 type comment, ``pyright:``
    for pyright and basedpyright, ``mypy:``, ``ty: ignore``, ``pyrefly:
    ignore``, ``zuban: ignore``, pyre, pytype), linters (``noqa``, ``ruff:``,
    ``pylint:``, ``nosec``, PyCharm's ``noinspection``, Semgrep's
    ``nosemgrep``, Fixit's ``lint-ignore`` and ``lint-fixme``), coverage
    (``pragma``), and formatters and import sorters (``fmt:``, ``yapf:``,
    ``isort:``, ``autopep8:``, pycln, codespell). Any ``#`` segment of the
    comment may hold it, as in ``# type: ignore  # noqa: E721``.
    """
    return _DIRECTIVE.search(text) is not None


@functools.lru_cache(maxsize=16)
def _exclusion_regex(pattern: str) -> Optional[re.Pattern[str]]:
    return re.compile(pattern, re.MULTILINE) if pattern else None


def _counts_as_directive(text: str, pattern: str) -> bool:
    """Whether a tool reads the comment ``text`` in a project coverage.py excludes by ``pattern``.

    A comment the project's own exclusion regexes match is coverage's, even
    one no other tool reads (``# nocov``); coverage's default ``# pragma: no
    cover`` in a project whose regexes leave it out tells no tool anything.
    """
    regex = _exclusion_regex(pattern)
    if regex is not None and regex.search(text):
        return True
    return is_directive(_DEFAULT_PRAGMA.sub("", text))


@functools.lru_cache(maxsize=16)
def exclusion_spans(source: str, pattern: str) -> Tuple[Tuple[int, int], ...]:
    """The first and last line of each match of coverage.py's joined exclusion regexes ``pattern``.

    As coverage.py's ``PythonParser.lines_matching`` finds them, a match
    spanning lines included; none for an empty ``pattern``, which excludes
    nothing.
    """
    regex = _exclusion_regex(pattern)
    if regex is None:
        return ()
    spans: List[Tuple[int, int]] = []
    last_start = last_start_line = 0
    for match in regex.finditer(source):
        start, end = match.span()
        start_line = last_start_line + source.count("\n", last_start, start)
        end_line = last_start_line + source.count("\n", last_start, end)
        spans.append((start_line + 1, end_line + 1))
        last_start, last_start_line = start, start_line
    return tuple(spans)


@functools.lru_cache(maxsize=16)
def excluded_lines(source: str, pattern: str) -> FrozenSet[int]:
    """The lines of ``source`` coverage.py's joined exclusion regexes ``pattern`` match.

    Every line a match touches, a match spanning lines included, as
    coverage.py's ``PythonParser.lines_matching`` counts them; none for an
    empty ``pattern``, which excludes nothing.
    """
    return frozenset(
        line for first, last in exclusion_spans(source, pattern) for line in range(first, last + 1)
    )


def is_file_directive(text: str) -> bool:
    """Whether the comment ``text`` configures a tool for its whole file, wherever it stands."""
    return _FILE_DIRECTIVE.search(text) is not None


def _normalized(text: str) -> str:
    """A directive as its tools read it: without spacing, which none of them weighs."""
    return re.sub(r"\s+", "", text)


# -- Tokens --------------------------------------------------------------------

_INSIGNIFICANT = frozenset(
    {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_OPENING = frozenset("([{")
_CLOSING = frozenset(")]}")
_CLAUSE_KEYWORDS = frozenset({"else", "elif", "except", "finally", "case"})
_HEADER_KEYWORDS = frozenset({"else", "try", "finally", "except"})


@dataclass(frozen=True)
class _Token:
    kind: int
    text: str
    start: Position
    end: Position


def _tokens(text: str) -> List[_Token]:
    """The significant tokens and the comments of ``text``, in order; raises on bad input."""
    return [
        _Token(token.type, token.string, token.start, token.end)
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type not in _INSIGNIFICANT
    ]


def _block_tokens(lines: Sequence[str], first: Position, last_line: int) -> Optional[List[_Token]]:
    """The tokens of the block from ``first`` to the end of ``last_line``, or None when unreadable.

    The block's lines are tokenized on their own, under a header that makes
    their indentation legal, since a block of a function body is indented;
    the whole text is the fallback for a block the header does not fit.
    """
    fragment = "if 1:\n" + "".join(lines[first[0] - 1 : last_line])
    shift = first[0] - 2
    try:
        shifted = [
            _Token(
                token.kind,
                token.text,
                (token.start[0] + shift, token.start[1]),
                (token.end[0] + shift, token.end[1]),
            )
            for token in _tokens(fragment)
            if token.start[0] > 1
        ]
    except (tokenize.TokenError, SyntaxError):
        try:
            shifted = [
                token
                for token in _tokens("".join(lines))
                if first[0] <= token.start[0] <= last_line
            ]
        except (tokenize.TokenError, SyntaxError):
            return None
    return [token for token in shifted if token.start >= first]


@functools.lru_cache(maxsize=8)
def _lines_of(source: str) -> Tuple[str, ...]:
    """``source`` split as the tokenizer counts lines, once per module text.

    Every generated call reads its block's comments from its module, and a
    module's pairs number in the thousands.
    """
    return tuple(source_lines(source))


def _column(line: str, byte_offset: int) -> int:
    """The character column of an AST column, which counts UTF-8 bytes."""
    if line.isascii():
        return byte_offset
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", errors="replace"))


# -- Reading a block's comments ------------------------------------------------


@dataclass(frozen=True)
class _Located:
    path: NodePath
    depth: int
    start: Position
    end: Position
    node: ast.AST


def _statement_like(node: ast.AST) -> bool:
    return isinstance(node, (ast.stmt, ast.ExceptHandler))


def _children(node: ast.AST) -> Iterable[Tuple[str, int, ast.AST]]:
    for field_name, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            yield field_name, -1, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, ast.AST):
                    yield field_name, index, item


class _BlockIndex:
    """Every located node of a block, by where it starts and ends in the source."""

    def __init__(self, block: Sequence[ast.stmt], lines: Sequence[str]) -> None:
        located: List[_Located] = []
        pending: List[Tuple[ast.AST, NodePath, int]] = [
            (statement, NodePath(index, type(statement).__name__), 0)
            for index, statement in enumerate(block)
        ]
        while pending:
            node, path, depth = pending.pop()
            lineno = getattr(node, "lineno", None)
            end_lineno = getattr(node, "end_lineno", None)
            col = getattr(node, "col_offset", None)
            end_col = getattr(node, "end_col_offset", None)
            if lineno is not None and end_lineno is not None and col is not None:
                if end_col is not None:
                    located.append(
                        _Located(
                            path,
                            depth,
                            (lineno, _column(lines[lineno - 1], col)),
                            (end_lineno, _column(lines[end_lineno - 1], end_col)),
                            node,
                        )
                    )
            # The parts of an f-string are placed unreliably before Python
            # 3.12, and no comment can stand inside one before it.
            if isinstance(node, ast.JoinedStr):
                continue
            for field_name, index, child in _children(node):
                pending.append(
                    (child, path.child(field_name, index, type(child).__name__), depth + 1)
                )
        self._by_start: Dict[Position, _Located] = {}
        self._by_end: Dict[Position, _Located] = {}
        self._innermost_by_end: Dict[Position, _Located] = {}
        for entry in located:
            if entry.start not in self._by_start or entry.depth < self._by_start[entry.start].depth:
                self._by_start[entry.start] = entry
            if entry.end not in self._by_end or entry.depth < self._by_end[entry.end].depth:
                self._by_end[entry.end] = entry
            innermost = self._innermost_by_end.get(entry.end)
            if innermost is None or entry.depth > innermost.depth:
                self._innermost_by_end[entry.end] = entry
        self._ends = sorted(self._by_end)
        self._starts_by_line: Dict[int, List[_Located]] = {}
        for entry in sorted(self._by_start.values(), key=lambda item: item.start):
            self._starts_by_line.setdefault(entry.start[0], []).append(entry)
        self._statements = [entry for entry in located if _statement_like(entry.node)]

    def starting_at(self, position: Position) -> Optional[_Located]:
        """The outermost node starting at ``position``."""
        return self._by_start.get(position)

    def innermost_ending_at(self, position: Position) -> Optional[_Located]:
        """The most deeply nested node ending at ``position``: the one a closing bracket there ends."""
        return self._innermost_by_end.get(position)

    def ending_at_or_before(self, position: Position) -> Optional[_Located]:
        """The outermost of the nodes ending last at or before ``position``."""
        index = bisect.bisect_right(self._ends, position) - 1
        return self._by_end[self._ends[index]] if index >= 0 else None

    def first_starting_on(self, line: int, before: int) -> Optional[_Located]:
        """The outermost node starting first on ``line``, before column ``before``."""
        for entry in self._starts_by_line.get(line, ()):
            if entry.start[1] < before:
                return entry
        return None

    def statement_closed_before(self, position: Position, column: int) -> Optional[_Located]:
        """The statement a comment at ``position`` indented to ``column`` closes, if it is deeper.

        Of the statements ending last before the comment (a statement and the
        compound statements it ends), the one indented furthest but not past
        the comment: the comment trails that statement's body.
        """
        ended = [entry for entry in self._statements if entry.end <= position]
        if not ended:
            return None
        last = max(entry.end for entry in ended)
        candidates = [entry for entry in ended if entry.end == last and entry.start[1] <= column]
        return max(candidates, key=lambda entry: entry.start[1]) if candidates else None

    def innermost_statement_on(self, line: int) -> Optional[_Located]:
        """The most deeply nested statement whose lines include ``line``."""
        holding = [entry for entry in self._statements if entry.start[0] <= line <= entry.end[0]]
        return max(holding, key=lambda entry: entry.depth) if holding else None

    def statement_starting_after(self, position: Position) -> Optional[_Located]:
        """The first statement starting after ``position``."""
        later = [entry for entry in self._statements if entry.start > position]
        return min(later, key=lambda entry: (entry.start, entry.depth)) if later else None

    def spanning(self, start: Position, end: Position) -> Optional[_Located]:
        """The outermost node starting at ``start`` and ending at ``end``."""
        entry = self._by_start.get(start)
        found: Optional[_Located] = None
        for candidate in (entry, self._by_end.get(end)):
            if candidate is not None and candidate.start == start and candidate.end == end:
                if found is None or candidate.depth < found.depth:
                    found = candidate
        return found


_EXPLODABLE = (ast.Call, ast.List, ast.Tuple, ast.Set, ast.Dict)


def _bracket_context(
    tokens: Sequence[_Token], at: int, index: _BlockIndex
) -> Tuple[Optional[NodePath], Optional[NodePath]]:
    """The grouping and the exploded node of the innermost brackets around token ``at``."""
    stack: List[int] = []
    for position in range(at):
        text = tokens[position].text
        if tokens[position].kind == tokenize.OP and text in _OPENING:
            stack.append(position)
        elif tokens[position].kind == tokenize.OP and text in _CLOSING and stack:
            stack.pop()
    if not stack:
        return None, None
    opening = stack[-1]
    depth = 0
    closing: Optional[int] = None
    for position in range(opening, len(tokens)):
        token = tokens[position]
        if token.kind != tokenize.OP:
            continue
        if token.text in _OPENING:
            depth += 1
        elif token.text in _CLOSING:
            depth -= 1
            if depth == 0:
                closing = position
                break
    if closing is None:
        return None, None
    inner = [token for token in tokens[opening + 1 : closing] if token.kind != tokenize.COMMENT]
    group: Optional[NodePath] = None
    if tokens[opening].text == "(" and inner:
        spanned = index.spanning(inner[0].start, inner[-1].end)
        if spanned is not None:
            group = spanned.path
    exploded: Optional[NodePath] = None
    if inner and inner[-1].text == ",":
        owner = index.innermost_ending_at(tokens[closing].end)
        if owner is not None and isinstance(owner.node, _EXPLODABLE):
            exploded = owner.path
    return group, exploded


def _between(tokens: Sequence[_Token], start: Position, end: Position) -> Tuple[str, ...]:
    return tuple(
        token.text
        for token in tokens
        if token.kind != tokenize.COMMENT and token.start >= start and token.end <= end
    )


def _trailing_anchor(
    tokens: Sequence[_Token],
    at: int,
    index: _BlockIndex,
    first_on_line: Optional[_Token],
    last: NodePath,
) -> Anchor:
    comment = tokens[at]
    line = comment.start[0]
    before = index.ending_at_or_before(comment.start)
    if before is not None and before.end[0] == line:
        return Anchor(before.path, Placement.TRAILING, _between(tokens, before.end, comment.start))
    if first_on_line is not None and first_on_line.text in _HEADER_KEYWORDS:
        opened = index.statement_starting_after(comment.start)
        if opened is not None:
            return Anchor(opened.path, Placement.HEADER)
    holder = index.innermost_statement_on(line) or index.statement_starting_after(comment.start)
    return Anchor(holder.path if holder is not None else last, Placement.FIRST_LINE)


def _own_line_anchor(
    tokens: Sequence[_Token], at: int, index: _BlockIndex, last: NodePath
) -> Anchor:
    comment = tokens[at]
    following = next((token for token in tokens[at + 1 :] if token.kind != tokenize.COMMENT), None)
    if following is None:
        closed = index.statement_closed_before(comment.start, comment.start[1])
        return Anchor(closed.path if closed is not None else last, Placement.CLOSING)
    starting = index.starting_at(following.start)
    clause = following.text in _CLAUSE_KEYWORDS and following.kind == tokenize.NAME
    if (starting is not None and _statement_like(starting.node)) or (clause and starting is None):
        if comment.start[1] > following.start[1]:
            # Indented past what follows: the comment trails the body it is in.
            closed = index.statement_closed_before(comment.start, comment.start[1])
            if closed is not None:
                return Anchor(closed.path, Placement.CLOSING)
        if starting is not None:
            return Anchor(starting.path, Placement.LEADING)
        opened = index.statement_starting_after(following.start)
        if opened is not None:
            return Anchor(opened.path, Placement.ABOVE_HEADER)
    if starting is not None:
        return Anchor(starting.path, Placement.LEADING)
    before = index.ending_at_or_before(comment.start)
    if before is not None:
        return Anchor(before.path, Placement.CLOSING, _between(tokens, before.end, comment.start))
    holder = index.statement_starting_after(comment.start)
    return Anchor(holder.path if holder is not None else last, Placement.LEADING)


def site_comments(
    source: str,
    block: Sequence[ast.stmt],
    argument_lines: Iterable[int] = (),
    exclusion: str = DEFAULT_EXCLUSION,
) -> SiteComments:
    """The comments written between the first and last line of ``block``, anchored in it.

    ``source`` is the module ``block`` was parsed from, and ``exclusion`` the
    joined regexes its project's coverage.py excludes lines by
    (``coverage_config``). Comments above the block's first statement or
    below its last line belong to the call site and stay there. A block
    whose tokens cannot be read is marked so, and moves nowhere
    (``directive_conflict``).
    """
    arguments = frozenset(argument_lines)
    if not block:
        return SiteComments(argument_lines=arguments)
    lines = _lines_of(source)
    first_line, last_line = block[0].lineno, block[-1].end_lineno or block[-1].lineno
    matched = excluded_lines(source, exclusion)
    in_block = frozenset(line for line in matched if first_line <= line <= last_line)
    beginning_outside, across = _exclusion_moves(source, exclusion, first_line, last_line)
    around = _around(source, block, matched) + (
        (Surrounding(beginning_outside, "coverage"),) if beginning_outside else ()
    )
    commented = any("#" in line for line in lines[first_line - 1 : last_line])
    index = _BlockIndex(block, lines) if in_block or commented else None
    excluded = _excluded(in_block, index)
    start = _excluded_start(block, in_block)
    directed = _directive_before(lines, first_line)
    shared = shared_lines(lines, block)
    bare = SiteComments(
        argument_lines=arguments,
        around=around,
        excluded=excluded,
        excluded_start=start,
        directed_start=directed,
        shared_line_directive=_shared_line_directive(shared, excluded, ()),
        excluded_across=across,
    )
    if index is None or not commented:
        return bare
    first = (first_line, _column(lines[first_line - 1], block[0].col_offset))
    tokens = _block_tokens(lines, first, last_line)
    if tokens is None:
        return SiteComments(
            argument_lines=arguments,
            unreadable=True,
            around=around,
            excluded=excluded,
            excluded_start=start,
            directed_start=directed,
            shared_line_directive=bare.shared_line_directive,
            excluded_across=across,
        )
    positions = [
        position for position, token in enumerate(tokens) if token.kind == tokenize.COMMENT
    ]
    if not positions:
        return bare
    last = NodePath(len(block) - 1, type(block[-1]).__name__)
    first_on_line: Dict[int, _Token] = {}
    for token in tokens:
        if token.kind != tokenize.COMMENT:
            first_on_line.setdefault(token.start[0], token)
    comments: List[BlockComment] = []
    for at in positions:
        comment = tokens[at]
        line = comment.start[0]
        own_line = not lines[line - 1][: comment.start[1]].strip()
        if own_line:
            anchor = _own_line_anchor(tokens, at, index, last)
            line_start = None
        else:
            anchor = _trailing_anchor(tokens, at, index, first_on_line.get(line), last)
            starter = index.first_starting_on(line, comment.start[1])
            line_start = starter.path if starter is not None else None
        group, exploded = _bracket_context(tokens, at, index)
        directive = _counts_as_directive(comment.text, exclusion)
        comments.append(
            BlockComment(
                comment.text.rstrip(),
                Anchor(anchor.node, anchor.placement, anchor.punctuation, group, exploded),
                line,
                line_start,
                _reach(tokens, at, own_line, index) if directive else frozenset(),
                directive,
                comment.start[1],
            )
        )
    moved = _with_region_reach(comments)
    return SiteComments(
        moved,
        arguments,
        around=around,
        excluded=excluded,
        excluded_start=start,
        directed_start=directed,
        shared_line_directive=_shared_line_directive(shared, excluded, moved),
        excluded_across=across,
    )


@functools.lru_cache(maxsize=8)
def _code_lines(source: str) -> FrozenSet[int]:
    """The lines of ``source`` holding code, which coverage.py measures: not blank, not only a comment."""
    try:
        tokens = _tokens(source)
    except (tokenize.TokenError, SyntaxError):
        return frozenset(range(1, len(_lines_of(source)) + 1))
    return frozenset(
        line
        for token in tokens
        if token.kind != tokenize.COMMENT
        for line in range(token.start[0], token.end[0] + 1)
    )


def _exclusion_moves(source: str, pattern: str, first: int, last: int) -> Tuple[str, str]:
    """What moving lines ``first`` to ``last`` out of ``source`` changes in what coverage.py excludes.

    A match of coverage.py's regexes may span lines, and one crossing the
    block's edge is cut by the move. Compared on the lines holding code:
    the block's lines excluded in the module but not on its own, where the
    helper will hold them (a match beginning outside it, as a region's
    marker would); and the lines outside it whose exclusion needed the
    block's text. Each is described, or empty when the move changes
    nothing there.
    """
    spans = exclusion_spans(source, pattern)
    if not any(start < first <= end or start <= last < end for start, end in spans):
        return "", ""
    lines = _lines_of(source)
    code = _code_lines(source)
    inside = range(first, last + 1)
    matched = excluded_lines(source, pattern)
    alone = {first - 1 + line for line in excluded_lines("".join(lines[first - 1 : last]), pattern)}
    in_place = {line for line in inside if line in code and line in matched}
    lost = in_place ^ {line for line in alone if line in code}
    blanked = "".join(lines[: first - 1]) + "\n" * (last - first + 1) + "".join(lines[last:])
    outside = {line for line in code if line not in inside}
    changed = (matched & outside) ^ (excluded_lines(blanked, pattern) & outside)
    return (
        (
            f"line {min(lost)} is excluded from coverage by a match beginning outside the block"
            if lost
            else ""
        ),
        (
            f"line {min(changed)} is excluded from coverage by a match reaching into the block"
            if changed
            else ""
        ),
    )


def _shared_line_directive(
    shared: FrozenSet[int], excluded: FrozenSet[int], comments: Sequence[BlockComment]
) -> str:
    """What governs a line of the block that also holds code outside it, if a tool reads anything there.

    ``shared`` are such lines (``splicing.shared_lines``). A directive governs
    its whole line, and the splice parts the block from the rest of that
    line: a comment there moves into the helper, leaving the code that
    stays uncovered, or stays with that code while the helper takes a copy.
    Coverage's exclusion of a line, by a pragma or by a configured regex,
    governs it alike. A comment no tool reads may move.
    """
    excluded_here = sorted(shared & excluded)
    if excluded_here:
        return f"line {excluded_here[0]} is excluded from coverage and holds code outside the block"
    governing = (
        comment
        for comment in comments
        if comment.directive and (comment.line in shared or comment.reach & shared)
    )
    found = next(governing, None)
    return "" if found is None else f"line {found.line}: {found.text}"


def _excluded(lines: FrozenSet[int], index: Optional[_BlockIndex]) -> FrozenSet[int]:
    """The block's lines coverage.py excludes: each excluded line's statement, and the clause it opens.

    coverage.py excludes a whole statement when any of its lines matches, and
    the whole suite after the colon of a statement it excludes.
    """
    if index is None:
        return frozenset()
    found: set[int] = set(lines)
    for line in lines:
        holder = index.innermost_statement_on(line)
        if holder is not None:
            found.update(range(min(holder.start[0], line), max(holder.end[0], line) + 1))
    return frozenset(found)


def _excluded_start(block: Sequence[ast.stmt], excluded: FrozenSet[int]) -> str:
    """Why coverage.py excludes the block's first statement, when that is a simple one.

    The call replacing the block runs exactly when its first statement did,
    and coverage measures it; a statement excluded because it never runs
    in the tests (the ``log(...)`` before a ``raise``) would leave that
    call unrun and measured. The header of a compound statement is left
    out: it runs whenever it is reached, and so does the call.
    """
    first = block[0]
    if getattr(first, "body", None) is not None:
        return ""
    lines = range(first.lineno, (first.end_lineno or first.lineno) + 1)
    hit = next((line for line in lines if line in excluded), None)
    return "" if hit is None else f"line {hit} is excluded from coverage"


@functools.lru_cache(maxsize=8)
def _module_comments(source: str) -> Optional[Tuple[ast.Module, Dict[int, Tuple[_Token, ...]]]]:
    """The module's tree and its comments by line; None when it does not parse or tokenize."""
    try:
        tree = ast.parse(source)
        tokens = _tokens(source)
    except (SyntaxError, ValueError, tokenize.TokenError):
        return None
    by_line: Dict[int, List[_Token]] = {}
    for token in tokens:
        if token.kind == tokenize.COMMENT:
            by_line.setdefault(token.start[0], []).append(token)
    return tree, {line: tuple(found) for line, found in by_line.items()}


def _clauses(node: ast.AST) -> List[List[ast.stmt]]:
    """The statement lists of a compound statement, in order: its body, branches and handlers."""
    found: List[List[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        value = getattr(node, name, None)
        if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
            found.append(list(value))
    for handler in getattr(node, "handlers", ()) or ():
        if isinstance(handler, ast.ExceptHandler) and handler.body:
            found.append(list(handler.body))
    for case in getattr(node, "cases", ()) or ():
        if isinstance(case, ast.match_case) and case.body:
            found.append(list(case.body))
    return found


def _scope_of(node: ast.stmt) -> Tuple[str, str]:
    if isinstance(node, ast.ClassDef):
        return "class", node.name
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function", node.name
    return "", ""


def _start_of(statement: ast.stmt) -> int:
    """The first line of ``statement``, its decorators included."""
    decorators: Sequence[ast.expr] = getattr(statement, "decorator_list", None) or []
    return min([statement.lineno, *(decorator.lineno for decorator in decorators)])


def _enclosing(tree: ast.Module, block: Sequence[ast.stmt]) -> List[ast.stmt]:
    """The statements holding ``block``, outermost first."""
    first, last = block[0].lineno, block[-1].end_lineno or block[-1].lineno
    starts = {(statement.lineno, statement.col_offset) for statement in block}
    holders: List[ast.stmt] = []
    node: ast.AST = tree
    while True:
        holder: Optional[ast.stmt] = None
        for clause in _clauses(node):
            for statement in clause:
                start, end = statement.lineno, statement.end_lineno or statement.lineno
                if (start, statement.col_offset) in starts:
                    continue
                if start <= first and last <= end:
                    holder = statement
        if holder is None:
            return holders
        holders.append(holder)
        node = holder


def _header_reach(holder: ast.stmt) -> FrozenSet[Home]:
    """The homes a directive governing all of ``holder`` from its header reaches: those inside it."""
    scope, name = _scope_of(holder)
    if scope == "class":
        return frozenset({("class", name), ("whole-class", name)})
    if scope == "function":
        return frozenset({("function", name)})
    return frozenset()


def _around(
    source: str, block: Sequence[ast.stmt], excluded: FrozenSet[int]
) -> Tuple[Surrounding, ...]:
    """Every directive outside ``block`` that reaches it, which a helper holding it may escape.

    A line of the header of a statement that encloses the block (its
    function's ``def`` line or decorators, a class, an ``if``, ``for``,
    ``else:`` or ``except`` line and the like) that coverage.py excludes,
    by a pragma or a configured regex (``excluded``), excludes the whole of
    it; so does pylint's ``disable`` at the end of such a line; and a
    region directive opened before the block and not closed before it
    reaches it (``_regions_around``). Whether the helper stays within one
    depends on where it is written (``Surrounding.governs``).
    """
    first = block[0].lineno
    if not excluded and _ANY_REGION.search(source) is None:
        return ()
    read = _module_comments(source)
    if read is None:
        return ()
    tree, comments = read
    lines = _lines_of(source)
    holders = _enclosing(tree, block)
    found: List[Surrounding] = []

    def own_line(token: _Token) -> bool:
        return not lines[token.start[0] - 1][: token.start[1]].strip()

    for holder in holders:
        holder_clauses = _clauses(holder)
        header_start = _start_of(holder)
        header_lines = (
            set(range(header_start, holder_clauses[0][0].lineno)) if holder_clauses else set()
        )
        previous_end = header_start
        for clause in holder_clauses:
            clause_start, clause_end = clause[0].lineno, clause[-1].end_lineno or clause[0].lineno
            if clause_start <= first <= clause_end:
                header_lines.update(range(previous_end + 1, clause_start))
            previous_end = clause_end
        reach = _header_reach(holder)
        for line in sorted(header_lines):
            if line in excluded:
                found.append(
                    Surrounding(f"line {line} is excluded from coverage", "coverage", reach)
                )
            for token in comments.get(line, ()):
                if not own_line(token) and _STATEMENT_WIDE.search(token.text):
                    found.append(
                        Surrounding(f"line {line}: {token.text.rstrip()}", "pylint", reach)
                    )
    found.extend(_regions_around(tree, holders, comments, lines, first))
    return tuple(found)


@dataclass(frozen=True)
class _Clause:
    """A statement list enclosing a block, and where its own comments stand."""

    statements: Sequence[ast.stmt]
    # The first line that may hold a comment of the clause's own: the one
    # after its header or the clause before it, or the module's first.
    begins: int
    ends: int
    column: int
    # Whether the block is in this clause, not in one before it.
    holds: bool

    def owns(self, line: int) -> bool:
        """Whether ``line`` stands between the clause's statements, not inside one of them."""
        return self.begins <= line <= self.ends and not any(
            _start_of(statement) <= line <= (statement.end_lineno or statement.lineno)
            for statement in self.statements
        )


def _clauses_around(
    tree: ast.Module, holders: Sequence[ast.stmt], first: int, total: int
) -> List[_Clause]:
    """The module and every clause of a statement holding line ``first``, outermost first.

    A clause of a compound statement before the one holding the block is
    listed too, as not holding it: pylint carries a pragma to the end of the
    statement it stands in, its later clauses included.
    """
    found = [_Clause(tree.body, 1, total, 0, True)]
    for holder in holders:
        previous_end = _start_of(holder)
        for clause in _clauses(holder):
            if clause[0].lineno > first:
                break
            clause_end = clause[-1].end_lineno or clause[-1].lineno
            found.append(
                _Clause(
                    clause,
                    previous_end + 1,
                    clause_end,
                    clause[0].col_offset,
                    clause[0].lineno <= first <= clause_end,
                )
            )
            previous_end = clause_end
    return found


def _left_open(tokens: Iterable[_Token], family: _Region, column: Optional[int]) -> List[_Token]:
    """The openers of ``family`` among ``tokens`` that no later closer of theirs closes.

    A closer counts only at ``column`` when one is given, and closes the
    latest opener it closes by its tool's reading.
    """
    opened: List[_Token] = []
    for token in tokens:
        if family.opens.search(token.text):
            opened.append(token)
        elif family.closes.search(token.text) and (column is None or token.start[1] == column):
            index = next(
                (
                    position
                    for position in range(len(opened) - 1, -1, -1)
                    if family.closed_by(opened[position].text, token.text)
                ),
                None,
            )
            if index is not None:
                del opened[index]
    return opened


def _regions_around(
    tree: ast.Module,
    holders: Sequence[ast.stmt],
    comments: Dict[int, Tuple[_Token, ...]],
    lines: Sequence[str],
    first: int,
) -> List[Surrounding]:
    """The region directives on lines of their own before line ``first`` whose regions reach it.

    Read as each family's tool reads them (``_Scope``): for a region that
    lasts to the end of its body, an opener between the statements of a
    clause enclosing the block, not closed at that clause's column before
    the block (pylint's also from an earlier clause of the same statement);
    for one that lasts line by line, any opener before the block not closed
    anywhere before it.
    """
    before = [
        token
        for line in sorted(line for line in comments if line < first)
        for token in comments[line]
        if not lines[token.start[0] - 1][: token.start[1]].strip()
    ]
    if not any(_ANY_REGION.search(token.text) for token in before):
        return []
    total = len(lines)
    clauses = _clauses_around(tree, holders, first, total)
    found: List[Surrounding] = []
    for family in _REGION_FAMILIES:
        opened: Dict[int, _Token] = {}
        if family.scope is not _Scope.FILE:
            for clause in clauses:
                if not clause.holds and family.family != "pylint":
                    continue
                owned = [token for token in before if clause.owns(token.start[0])]
                for token in _left_open(owned, family, clause.column):
                    opened[token.start[0]] = token
        if family.scope is not _Scope.BLOCK:
            for token in _left_open(before, family, None):
                opened[token.start[0]] = token
        for line, token in sorted(opened.items()):
            found.append(
                Surrounding(
                    f"line {line}: {token.text.rstrip()}",
                    family.family,
                    _homes_reached(line, family, tree, holders, comments, first),
                    family.layout,
                )
            )
    return found


def _homes_reached(
    opener: int,
    family: _Region,
    tree: ast.Module,
    holders: Sequence[ast.stmt],
    comments: Dict[int, Tuple[_Token, ...]],
    first: int,
) -> FrozenSet[Home]:
    """Where a helper stays in the region opened on line ``opener`` that reaches line ``first``.

    A module helper is written before the module's first definition or
    after it, a method helper after the method holding its first call or
    at the end of the class, and a nested helper before its function's
    first statement (``insertion``). The region must be open over each
    such place: opened before it, and not closed, by anything its tool
    might read as a closer, before the end of what it must cover.
    """

    def closed_after(low: int, high: int) -> bool:
        return any(
            family.closes.search(token.text)
            for line in range(low + 1, high + 1)
            for token in comments.get(line, ())
        )

    homes: set[Home] = set()
    definitions = [
        _start_of(statement)
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    # A module helper goes no later than after the module's last statement.
    code_ends = max(
        (statement.end_lineno or statement.lineno for statement in tree.body), default=0
    )
    if definitions and opener < min(definitions) and not closed_after(opener, code_ends):
        homes.add(("module", ""))
    for position, holder in enumerate(holders):
        if isinstance(holder, ast.ClassDef):
            if closed_after(opener, holder.end_lineno or holder.lineno):
                continue
            inner = holders[position + 1] if position + 1 < len(holders) else None
            if inner is not None and opener < _start_of(inner):
                homes.add(("class", holder.name))
            if holder.body and opener < _start_of(holder.body[0]):
                homes.add(("whole-class", holder.name))
        elif isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                holder.body
                and opener < _start_of(holder.body[0])
                and not closed_after(opener, first - 1)
            ):
                homes.add(("function", holder.name))
    return frozenset(homes)


def _lines_between(first: int, last: int) -> FrozenSet[int]:
    return frozenset(range(first, last + 1))


def _reach(tokens: Sequence[_Token], at: int, own_line: bool, index: _BlockIndex) -> FrozenSet[int]:
    """The lines of the block whose code the directive ``tokens[at]`` governs (``BlockComment.reach``).

    A directive at the end of a line governs that line; pylint's
    ``disable`` governs the whole statement holding it, and the whole block
    when the line opens one. Of those on a line of their own,
    PyCharm's ``noinspection`` governs the statement after it, an ignore
    that a checker or linter applies to the next line governs that line
    (``_next_line_reach``), and a region directive its region
    (``_with_region_reach``); the rest govern nothing. A directive for the
    whole file reaches its module wherever the code goes.
    """
    comment = tokens[at]
    text, line = comment.text, comment.start[0]
    if not is_directive(text) or is_file_directive(text):
        return frozenset()
    if own_line:
        if _NEXT_STATEMENT.search(text):
            following = index.statement_starting_after(comment.start)
            if following is not None:
                return _lines_between(following.start[0], following.end[0])
        if _NEXT_LINE.search(text):
            return _next_line_reach(tokens, at, index)
        return frozenset()
    if _STATEMENT_WIDE.search(text):
        holder = index.innermost_statement_on(line)
        if holder is not None:
            return _lines_between(min(holder.start[0], line), max(holder.end[0], line))
    return frozenset({line})


def _next_line_reach(tokens: Sequence[_Token], at: int, index: _BlockIndex) -> FrozenSet[int]:
    """The lines an ignore on a line of its own, ``tokens[at]``, governs: the next line of code.

    Before a statement that is its logical line: the whole of a simple
    statement, and of a compound one its header up to the line its body
    starts on, as ty and ruff apply it (pyre, pyrefly, Semgrep and Fixit
    apply it to the first of those lines). Inside brackets it is the next
    physical line holding code.
    """
    following = next((token for token in tokens[at + 1 :] if token.kind != tokenize.COMMENT), None)
    if following is None:
        return frozenset()
    line = following.start[0]
    starting = index.starting_at(following.start)
    if starting is None or not _statement_like(starting.node):
        return frozenset({line})
    clauses = _clauses(starting.node)
    last = starting.end[0] if not clauses else max(line, clauses[0][0].lineno - 1)
    return _lines_between(line, last)


def _directive_before(lines: Sequence[str], first_line: int) -> str:
    """The ignore above line ``first_line`` that governs the code there, or empty.

    An ignore on a line of its own governs the next line of code
    (``_NEXT_LINE``, ``_NEXT_STATEMENT``), across the blank and comment
    lines between. Above a block's first statement it stays with the call
    that replaces the block, and would govern the call instead.
    """
    line = first_line - 1
    while line >= 1:
        text = lines[line - 1].strip()
        if text and not text.startswith("#"):
            return ""
        if _NEXT_LINE.search(text) or _NEXT_STATEMENT.search(text):
            return f"line {line}: {text} governs the block's first statement"
        line -= 1
    return ""


def _body_of(path: NodePath) -> Tuple[object, ...]:
    """The statement list holding the node at ``path``: the block's own, or a field of a node in it."""
    if not path.steps:
        return ()
    field_name = path.steps[-1][0]
    return (path.statement, path.kind, path.steps[:-1], field_name)


def _same_level(opener: BlockComment, closer: BlockComment, family: _Region) -> bool:
    """Whether ``closer`` stands where its tool lets it close ``opener``'s region.

    A region that lasts to the end of its body is closed only at the
    opener's column in the same body; one read line by line, anywhere.
    """
    if family.scope is _Scope.FILE:
        return True
    return opener.column == closer.column and _body_of(opener.anchor.node) == _body_of(
        closer.anchor.node
    )


def _region_pairs(
    comments: Sequence[BlockComment],
) -> Tuple[Dict[int, FrozenSet[int]], Optional[BlockComment]]:
    """The lines each region of ``comments`` closed within them reaches, and the first left unmatched.

    Keyed by the positions of both its opener and its closer, and read for
    every family whose tool reads the comment. An opener left open, or a
    comment its tool might read as a closer that closes nothing here, is a
    region crossing the block's edge (``directive_outlives_block``).
    """
    reach: Dict[int, FrozenSet[int]] = {}
    unmatched: List[BlockComment] = []
    for family in _REGION_FAMILIES:
        opened: List[int] = []
        for position, comment in enumerate(comments):
            if not comment.anchor.own_line:
                continue
            if family.opens.search(comment.text):
                opened.append(position)
            elif family.closes.search(comment.text):
                index = next(
                    (
                        candidate
                        for candidate in range(len(opened) - 1, -1, -1)
                        if _same_level(comments[opened[candidate]], comment, family)
                        and family.closed_by(comments[opened[candidate]].text, comment.text)
                    ),
                    None,
                )
                if index is None:
                    unmatched.append(comment)
                    continue
                start = opened.pop(index)
                region = _lines_between(comments[start].line, comment.line)
                reach[start] = reach.get(start, frozenset()) | region
                reach[position] = reach.get(position, frozenset()) | region
        unmatched.extend(comments[position] for position in opened)
    return reach, min(unmatched, key=lambda comment: comment.line) if unmatched else None


def _with_region_reach(comments: Sequence[BlockComment]) -> Tuple[BlockComment, ...]:
    """``comments`` with each region directive reaching from its opening to its closing line.

    A region left open, or closed without being opened, reaches past the
    block and declines it anyway (``_region_imbalance``).
    """
    reach, _ = _region_pairs(comments)
    return tuple(
        (
            BlockComment(
                comment.text,
                comment.anchor,
                comment.line,
                comment.line_start,
                reach[position],
                comment.directive,
                comment.column,
            )
            if position in reach
            else comment
        )
        for position, comment in enumerate(comments)
    )


def _moves_to_the_call(expression: ast.AST) -> bool:
    """Whether a site's argument is code a directive could have covered: not a name or literal."""
    if isinstance(expression, (ast.Name, ast.Constant)):
        return False
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return False
    return True


def argument_lines(expressions: Iterable[ast.AST]) -> FrozenSet[int]:
    """The lines of the site's expressions its call passes, where they are code, not a name."""
    lines: set[int] = set()
    for expression in expressions:
        lineno = getattr(expression, "lineno", None)
        if lineno is None or not _moves_to_the_call(expression):
            continue
        lines.update(range(lineno, (getattr(expression, "end_lineno", None) or lineno) + 1))
    return frozenset(lines)


def call_argument_lines(substitution: Substitution, block_index: int) -> FrozenSet[int]:
    """The lines where block ``block_index``'s own code becomes an argument of its call.

    Each parameter the unifier introduced is passed that block's expression,
    as it is or inside a lambda, and a promoted literal its original one;
    both are written at the call site from then on.
    """
    expressions: List[ast.AST] = [
        expression
        for pairs in substitution.param_expressions.values()
        for index, expression in pairs
        if index == block_index
    ]
    expressions.extend(
        by_block[block_index]
        for by_block in substitution.promoted_literal_args.values()
        if block_index in by_block
    )
    return argument_lines(expressions)


# -- Deciding the helper's comments --------------------------------------------


def _child(node: ast.AST, field_name: str, index: int) -> Optional[ast.AST]:
    value = getattr(node, field_name, None)
    if index < 0:
        return value if isinstance(value, ast.AST) else None
    if isinstance(value, list) and index < len(value) and isinstance(value[index], ast.AST):
        item: ast.AST = value[index]
        return item
    return None


def _resolve(statements: Sequence[ast.AST], path: NodePath) -> Tuple[ast.AST, NodePath, bool]:
    """The node of ``statements`` at ``path``, its path there, and whether it stands where ``path`` does.

    Where the helper holds a parameter instead of the site's code, the path
    ends at the parameter, which stands in that code's place: exactly where
    the anchor was when it is the anchor that the parameter replaced, and
    only near it when the anchor was inside the replaced code. A path also
    ends early where an annotation variant respelled a subtree.
    """
    if path.statement >= len(statements):
        raise CommentPlacementError(
            f"The helper's body has no statement {path.statement} for a comment"
        )
    node = statements[path.statement]
    if type(node).__name__ != path.kind:
        return node, NodePath(path.statement, type(node).__name__), False
    for depth, (field_name, index, kind) in enumerate(path.steps):
        child = _child(node, field_name, index)
        if child is None:
            return node, path.prefix(depth), False
        if type(child).__name__ != kind:
            replaced = path.prefix(depth).child(field_name, index, type(child).__name__)
            return child, replaced, depth == len(path.steps) - 1
        node = child
    return node, path, True


def _projected(statements: Sequence[ast.AST], anchor: Anchor) -> Tuple[Anchor, bool]:
    """``anchor`` as it lands in ``statements``, and whether its node is there whole."""
    _, node, exact = _resolve(statements, anchor.node)
    group = _resolve(statements, anchor.group)[1] if anchor.group is not None else None
    exploded: Optional[NodePath] = None
    if anchor.exploded is not None and _resolve(statements, anchor.exploded)[2]:
        exploded = anchor.exploded
    punctuation = anchor.punctuation if exact else ()
    return Anchor(node, anchor.placement, punctuation, group, exploded), exact


_Place = Tuple[NodePath, Placement, Tuple[str, ...]]
"""Where a comment lands in the helper, whatever the brackets around it."""


def _place(anchor: Anchor) -> _Place:
    return anchor.node, anchor.placement, anchor.punctuation


def _directives(
    statements: Sequence[ast.AST], site: SiteComments
) -> Dict[Tuple[_Place, str], List[BlockComment]]:
    """The site's directives by where they land in the helper and how their tools read them."""
    found: Dict[Tuple[_Place, str], List[BlockComment]] = {}
    for comment in site.comments:
        if comment.directive:
            anchor, _ = _projected(statements, comment.anchor)
            found.setdefault((_place(anchor), _normalized(comment.text)), []).append(comment)
    return found


def _region_imbalance(site: SiteComments) -> Optional[BlockComment]:
    """The first region directive of site whose region crosses the block's edge."""
    return _region_pairs(site.comments)[1]


def directive_conflict(
    statements: Sequence[ast.AST], sites: Sequence[SiteComments]
) -> Optional[CommentConflict]:
    """Why the sites' tool directives cannot move into the helper's ``statements``, if they cannot.

    Every site must carry the same directives, written alike up to spacing,
    at the same places; no directive may stand on a line a site's block
    shares with code outside it; no directive may reach code of a site's
    that becomes an argument of its call, which is written at the call site,
    where the directive does not reach; no ignore above a site's block may
    govern its first statement, since it stays above the call that takes
    the statement's place; and a region a directive opens or closes must
    not reach past the block.
    """
    for site in sites:
        if site.unreadable:
            return CommentConflict(
                ConflictKind.DIRECTIVES_DIFFER, "a site's comments cannot be tokenized"
            )
        if site.shared_line_directive:
            return CommentConflict(
                ConflictKind.DIRECTIVE_ON_SHARED_LINE, site.shared_line_directive
            )
    if sites:
        reference = _directives(statements, sites[0])
        for site in sites[1:]:
            found = _directives(statements, site)
            for key in sorted(
                set(reference) | set(found), key=lambda key: (key[1], key[0][1].value)
            ):
                ours, theirs = reference.get(key, []), found.get(key, [])
                if len(ours) != len(theirs):
                    example = (ours or theirs)[0]
                    return CommentConflict(
                        ConflictKind.DIRECTIVES_DIFFER,
                        f"{example.text} (line {example.line}) is not at every site",
                    )
    for site in sites:
        if site.excluded_start:
            return CommentConflict(ConflictKind.EXCLUDED_BLOCK_START, site.excluded_start)
        if site.directed_start:
            return CommentConflict(ConflictKind.DIRECTIVE_AROUND_BLOCK, site.directed_start)
        for comment in site.comments:
            if comment.reach & site.argument_lines:
                return CommentConflict(
                    ConflictKind.DIRECTIVE_ON_ARGUMENT,
                    f"line {comment.line}: {comment.text}",
                )
        uncovered = site.excluded & site.argument_lines
        if uncovered:
            return CommentConflict(
                ConflictKind.DIRECTIVE_ON_ARGUMENT,
                f"line {min(uncovered)} is excluded from coverage",
            )
        crossing = _region_imbalance(site)
        if crossing is not None:
            return CommentConflict(
                ConflictKind.DIRECTIVE_OUTLIVES_BLOCK, f"line {crossing.line}: {crossing.text}"
            )
        if site.excluded_across:
            return CommentConflict(ConflictKind.DIRECTIVE_OUTLIVES_BLOCK, site.excluded_across)
    return None


def merge_comments(
    statements: Sequence[ast.AST],
    sites: Sequence[Tuple[SiteComments, bool]],
    body_offset: int,
    home_class: Optional[str] = None,
    home_function: Optional[str] = None,
) -> Union[HelperComments, CommentConflict]:
    """The comments the helper carries from every site, or why the sites decline it.

    ``statements`` is the helper's body from ``body_offset`` on, and each
    site comes with whether it is in the helper's module. The directives
    must agree (``directive_conflict``), and a file-wide one must stay in its
    module. A directive around a site's block that would not reach the
    helper where it is written, in ``home_class`` or ``home_function`` or at
    module level (``Surrounding.governs``), declines the pair: one keeping a
    formatter off the block's layout wherever a site has one, since that
    layout would be formatted in the helper; a coverage exclusion or a
    linter's only when every site has one for that tool, since the helper
    is the code of a site its tool already measured or linted, so only then
    would it report anything new. The first site's directives are carried.
    Every other comment is carried from every site that has it: the first
    site's in its order, then each other site's that the ones before did
    not already carry at that place, on a line of its own before that code
    where one of theirs ends its line.
    """
    conflict = directive_conflict(statements, [site for site, _ in sites])
    if conflict is not None:
        return conflict
    for tool in sorted({surrounding.tool for site, _ in sites for surrounding in site.around}):
        escaped = [
            next(
                (
                    surrounding
                    for surrounding in site.around
                    if surrounding.tool == tool
                    and not (same_module and surrounding.governs(home_class, home_function))
                ),
                None,
            )
            for site, same_module in sites
        ]
        found = [surrounding for surrounding in escaped if surrounding is not None]
        # A formatter kept off one site's layout would format it in a helper
        # it does not reach; a linter silenced at one site reports nothing
        # new while another site, whose code the helper is too, is outside.
        if found and (found[0].layout or len(found) == len(sites)):
            return CommentConflict(ConflictKind.DIRECTIVE_AROUND_BLOCK, found[0].detail)
    for site, same_module in sites:
        if same_module:
            continue
        for comment in site.comments:
            if is_file_directive(comment.text):
                return CommentConflict(
                    ConflictKind.DIRECTIVE_OUTLIVES_BLOCK,
                    f"line {comment.line}: {comment.text} would configure another module",
                )
    by_place: Dict[_Place, List[HelperComment]] = {}
    for position, (site, _) in enumerate(sites):
        carried: Dict[_Place, Counter[str]] = {
            place: Counter(comment.text for comment in comments)
            for place, comments in by_place.items()
        }
        for comment in site.comments:
            directive = comment.directive
            if directive and position > 0:
                continue  # the same as the first site's, which are carried
            anchor, _ = _projected(statements, comment.anchor)
            already = carried.setdefault(_place(anchor), Counter())
            if already[comment.text] > 0:
                already[comment.text] -= 1
                continue
            line_start = None
            if directive and comment.line_start is not None:
                line_start = _resolve(statements, comment.line_start)[1]
            if position > 0 and not anchor.own_line and by_place.get(_place(anchor)):
                # Another site's note already ends that line; this one goes
                # on its own line before the same code rather than after it.
                anchor = _above(anchor)
            by_place.setdefault(_place(anchor), []).append(
                HelperComment(comment.text, anchor, directive, line_start)
            )
    return HelperComments(
        body_offset, tuple(comment for comments in by_place.values() for comment in comments)
    )


def _above(anchor: Anchor) -> Anchor:
    """``anchor`` moved from the end of its line to a line of its own before the same code."""
    placement = (
        Placement.ABOVE_HEADER if anchor.placement is Placement.HEADER else Placement.LEADING
    )
    return Anchor(anchor.node, placement, (), anchor.group, anchor.exploded)


# -- Writing the comments into the rendered helper -----------------------------


class _Rank(Enum):
    """The order of insertions at one offset of the rendered text."""

    AFTER_STATEMENT = 0
    ABOVE_HEADER = 1
    BEFORE_STATEMENT = 2
    OPEN_PAREN = 3
    COMMA = 4
    BEFORE_EXPRESSION = 5
    TRAILING = 6
    AFTER_EXPRESSION = 7
    CLOSE_PAREN = 8
    # A comment ending a line goes after a parenthesis written back there.
    END_OF_LINE = 9


@dataclass(frozen=True)
class _Insertion:
    offset: int
    rank: _Rank
    ordinal: int
    text: str
    # The comments this insertion writes; trailing ones at one place share a line.
    comments: Tuple[int, ...] = ()
    # Spaces of the rendered text at ``offset`` that the insertion replaces,
    # so that a line it breaks neither ends nor goes on with stray blanks.
    skip: int = 0


class _Layout:
    """Offsets, brackets and strings of a rendered text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.split("\n")
        self.line_starts: List[int] = []
        offset = 0
        for line in self.lines:
            self.line_starts.append(offset)
            offset += len(line) + 1
        self._token_ends: List[int] = []
        self._depth_after: List[int] = []
        self._strings: List[Tuple[int, int]] = []
        depth = 0
        fstring_start: List[int] = []
        for token in _tokens(text):
            start, end = self.offset(token.start), self.offset(token.end)
            if _FSTRING_START is not None and token.kind == _FSTRING_START:
                fstring_start.append(start)
                continue
            if _FSTRING_END is not None and token.kind == _FSTRING_END:
                opened = fstring_start.pop()
                if not fstring_start:
                    self._strings.append((opened, end))
                    self._token_ends.append(end)
                    self._depth_after.append(depth)
                continue
            if fstring_start:
                continue
            if token.kind == tokenize.STRING:
                self._strings.append((start, end))
            elif token.kind == tokenize.OP and token.text in _OPENING:
                depth += 1
            elif token.kind == tokenize.OP and token.text in _CLOSING:
                depth -= 1
            self._token_ends.append(end)
            self._depth_after.append(depth)

    def offset(self, position: Position) -> int:
        return self.line_starts[position[0] - 1] + position[1]

    def start_of(self, node: ast.AST) -> int:
        line = getattr(node, "lineno")
        return self.offset((line, _column(self.lines[line - 1], getattr(node, "col_offset"))))

    def end_of(self, node: ast.AST) -> int:
        line = getattr(node, "end_lineno")
        return self.offset((line, _column(self.lines[line - 1], getattr(node, "end_col_offset"))))

    def depth_at(self, offset: int) -> int:
        index = bisect.bisect_right(self._token_ends, offset) - 1
        return self._depth_after[index] if index >= 0 else 0

    def inside_string(self, offset: int) -> bool:
        return any(start < offset < end for start, end in self._strings)

    def line_end(self, offset: int) -> int:
        end = self.text.find("\n", offset)
        return len(self.text) if end < 0 else end

    def rest_is_blank(self, offset: int) -> bool:
        return not self.text[offset : self.line_end(offset)].strip()

    def after_punctuation(self, offset: int, punctuation: Sequence[str]) -> int:
        """``offset`` moved past the rendered tokens that match ``punctuation`` in order.

        A closing parenthesis the rendering lacks closed grouping parentheses
        it dropped, and is passed over; any other difference ends the match.
        """
        position = offset
        for expected in punctuation:
            rest = self.text[position:]
            stripped = rest.lstrip(" ")
            if stripped.startswith(expected) and _whole_token(stripped, expected):
                position += len(rest) - len(stripped) + len(expected)
            elif expected != ")":
                break
        return position

    def indentation(self, line: int) -> str:
        text = self.lines[line - 1]
        return text[: len(text) - len(text.lstrip(" \t"))]


def _whole_token(rest: str, expected: str) -> bool:
    """Whether ``rest`` begins with the token ``expected``, not a longer name that starts alike."""
    if not (expected[-1:].isalnum() or expected[-1:] == "_"):
        return True
    following = rest[len(expected) : len(expected) + 1]
    return not (following.isalnum() or following == "_")


def _statement_on_path(statements: Sequence[ast.AST], path: NodePath) -> ast.AST:
    """The innermost statement (or ``except`` clause) along ``path``."""
    node = statements[path.statement]
    found = node
    for field_name, index, _ in path.steps:
        child = _child(node, field_name, index)
        if child is None:
            break
        node = child
        if _statement_like(node):
            found = node
    return found


def _header_line(clause_first: ast.AST) -> int:
    """The rendered line of the keyword opening the clause whose first statement is given."""
    return int(getattr(clause_first, "lineno")) - 1


class _Weaver:
    """The insertions that write a helper's comments into its unparsed text."""

    def __init__(
        self, layout: _Layout, statements: Sequence[ast.AST], comments: Sequence[HelperComment]
    ) -> None:
        self.layout = layout
        self.statements = statements
        self.comments = comments
        self.insertions: List[_Insertion] = []
        self._groups: set[Tuple[int, int]] = set()
        self._commas: set[int] = set()

    def weave(self, safe: bool) -> List[_Insertion]:
        for ordinal, comment in enumerate(self.comments):
            if safe:
                self._safe(ordinal, comment)
            else:
                self._place(ordinal, comment)
        return self.insertions

    # Placement where the comment was ------------------------------------------

    def _place(self, ordinal: int, comment: HelperComment) -> None:
        anchor = comment.anchor
        node, _, exact = _resolve(self.statements, anchor.node)
        placement = anchor.placement
        layout = self.layout
        if placement is Placement.TRAILING:
            offset = layout.end_of(node)
            if exact and anchor.punctuation:
                offset = layout.after_punctuation(offset, anchor.punctuation)
            if layout.inside_string(offset):
                self._safe(ordinal, comment)
            elif layout.depth_at(offset) == 0 and self._regroup(offset, anchor, before=False):
                # Inside the parentheses the source wrapped the code in, the
                # comment stays on that code's line when a formatter splits it.
                self._explode(anchor)
                self._trailing(offset, ordinal, comment, continues=True)
            elif layout.rest_is_blank(offset):
                self._trailing(layout.line_end(offset), ordinal, comment)
            elif layout.depth_at(offset) > 0:
                self._explode(anchor)
                self._trailing(offset, ordinal, comment, continues=True)
            else:
                self._safe(ordinal, comment)
        elif placement is Placement.FIRST_LINE:
            statement = _statement_on_path(self.statements, anchor.node)
            self._trailing(layout.line_end(layout.start_of(statement)), ordinal, comment)
        elif placement is Placement.HEADER:
            line = _header_line(node)
            self._trailing(layout.line_end(layout.line_starts[line - 1]), ordinal, comment)
        elif placement is Placement.ABOVE_HEADER:
            line = _header_line(node)
            self._own_line(
                layout.line_starts[line - 1],
                layout.indentation(line),
                _Rank.ABOVE_HEADER,
                ordinal,
                comment,
            )
        elif _statement_like(node):
            if placement is Placement.LEADING:
                self._before_statement(node, ordinal, comment)
            else:
                self._after_statement(node, ordinal, comment)
        elif placement is Placement.LEADING:
            offset = layout.start_of(node)
            if self._bracketed(offset, anchor, before=True):
                self._explode(anchor)
                self._inside(offset, _Rank.BEFORE_EXPRESSION, ordinal, comment, node)
            else:
                self._safe(ordinal, comment)
        else:
            offset = layout.end_of(node)
            if exact and anchor.punctuation:
                offset = layout.after_punctuation(offset, anchor.punctuation)
            if self._bracketed(offset, anchor, before=False):
                self._explode(anchor)
                self._inside(offset, _Rank.AFTER_EXPRESSION, ordinal, comment, node)
            else:
                self._safe(ordinal, comment)

    def _bracketed(self, offset: int, anchor: Anchor, before: bool) -> bool:
        """Whether a line may break at ``offset``: inside brackets, or inside the source's parentheses."""
        layout = self.layout
        if layout.inside_string(offset):
            return False
        return layout.depth_at(offset) > 0 or self._regroup(offset, anchor, before)

    def _regroup(self, offset: int, anchor: Anchor, before: bool) -> bool:
        """Write back the grouping parentheses the source had around the comment, if they hold ``offset``.

        Unparsing drops parentheses that only group; the comment stood
        inside them, so they go back around their expression.
        """
        if anchor.group is None:
            return False
        group, _, _ = _resolve(self.statements, anchor.group)
        if _statement_like(group):
            return False
        start, end = self.layout.start_of(group), self.layout.end_of(group)
        if not (start <= offset < end if before else start < offset <= end):
            return False
        if (start, end) not in self._groups:
            self._groups.add((start, end))
            self.insertions.append(_Insertion(start, _Rank.OPEN_PAREN, -1, "("))
            self.insertions.append(_Insertion(end, _Rank.CLOSE_PAREN, -1, ")"))
        return True

    def _explode(self, anchor: Anchor) -> None:
        """Write back the trailing comma that held the comment's brackets open in the source."""
        if anchor.exploded is None:
            return
        node, _, exact = _resolve(self.statements, anchor.exploded)
        if not exact or not isinstance(node, _EXPLODABLE):
            return
        end = self.layout.end_of(node)
        closing = self.layout.text[end - 1 : end]
        inside = self.layout.text[self.layout.start_of(node) : end - 1].rstrip()
        if closing not in _CLOSING or inside.endswith((",", "(", "[", "{")):
            return
        if end - 1 not in self._commas:
            self._commas.add(end - 1)
            self.insertions.append(_Insertion(end - 1, _Rank.COMMA, -1, ","))

    def _continuation(self, comment: HelperComment) -> str:
        """The indentation of a line a comment breaks: one level inside its statement."""
        statement = _statement_on_path(self.statements, comment.anchor.node)
        return " " * (int(getattr(statement, "col_offset", 0)) + 4)

    def _blanks_after(self, offset: int) -> int:
        text = self.layout.text
        end = offset
        while end < len(text) and text[end] == " ":
            end += 1
        return end - offset

    def _trailing(
        self, offset: int, ordinal: int, comment: HelperComment, continues: bool = False
    ) -> None:
        if continues:
            self.insertions.append(
                _Insertion(
                    offset,
                    _Rank.TRAILING,
                    ordinal,
                    "  " + comment.text + "\n" + self._continuation(comment),
                    (ordinal,),
                    self._blanks_after(offset),
                )
            )
        else:
            self.insertions.append(
                _Insertion(offset, _Rank.END_OF_LINE, ordinal, "  " + comment.text, (ordinal,))
            )

    def _own_line(
        self, offset: int, indent: str, rank: _Rank, ordinal: int, comment: HelperComment
    ) -> None:
        self.insertions.append(
            _Insertion(offset, rank, ordinal, indent + comment.text + "\n", (ordinal,))
        )

    def _inside(
        self, offset: int, rank: _Rank, ordinal: int, comment: HelperComment, node: ast.AST
    ) -> None:
        indent = self._continuation(comment)
        text = self.layout.text
        start = offset
        if rank is _Rank.BEFORE_EXPRESSION:
            while start > 0 and text[start - 1] == " ":
                start -= 1
        self.insertions.append(
            _Insertion(
                start,
                rank,
                ordinal,
                "\n" + indent + comment.text + "\n" + indent,
                (ordinal,),
                offset - start if rank is _Rank.BEFORE_EXPRESSION else self._blanks_after(offset),
            )
        )

    def _before_statement(self, node: ast.AST, ordinal: int, comment: HelperComment) -> None:
        line = int(getattr(node, "lineno"))
        self._own_line(
            self.layout.line_starts[line - 1],
            self.layout.indentation(line),
            _Rank.BEFORE_STATEMENT,
            ordinal,
            comment,
        )

    def _after_statement(self, node: ast.AST, ordinal: int, comment: HelperComment) -> None:
        line = int(getattr(node, "end_lineno"))
        indent = self.layout.indentation(int(getattr(node, "lineno")))
        if line < len(self.layout.lines):
            self._own_line(
                self.layout.line_starts[line], indent, _Rank.AFTER_STATEMENT, ordinal, comment
            )
        else:
            self.insertions.append(
                _Insertion(
                    len(self.layout.text),
                    _Rank.AFTER_STATEMENT,
                    ordinal,
                    "\n" + indent + comment.text,
                    (ordinal,),
                )
            )

    # Placement that always holds ----------------------------------------------

    def _safe(self, ordinal: int, comment: HelperComment) -> None:
        """Place ``comment`` by its statement alone: on a line of its own, or ending one."""
        statement = _statement_on_path(self.statements, comment.anchor.node)
        if comment.anchor.placement is Placement.CLOSING:
            self._after_statement(statement, ordinal, comment)
        elif comment.anchor.own_line:
            self._before_statement(statement, ordinal, comment)
        else:
            body = getattr(statement, "body", None)
            if isinstance(body, list) and body:
                line = _header_line(body[0])
            else:
                line = int(getattr(statement, "end_lineno"))
            self._trailing(
                self.layout.line_end(self.layout.line_starts[line - 1]), ordinal, comment
            )


def _joined(insertions: Sequence[_Insertion]) -> List[_Insertion]:
    """The insertions with trailing comments at one place written on one line, in order."""
    joined: List[_Insertion] = []
    ordered = sorted(insertions, key=lambda item: (item.offset, item.rank.value, item.ordinal))
    for insertion in ordered:
        previous = joined[-1] if joined else None
        if (
            previous is not None
            and insertion.rank in (_Rank.TRAILING, _Rank.END_OF_LINE)
            and previous.rank is insertion.rank
            and previous.offset == insertion.offset
        ):
            # A comment breaking a line ends in the line break and indentation
            # that go on with the code; the joined comments share one.
            head, newline, rest = previous.text.partition("\n")
            text = head + insertion.text.partition("\n")[0] + newline + rest
            joined[-1] = _Insertion(
                previous.offset,
                previous.rank,
                previous.ordinal,
                text,
                previous.comments + insertion.comments,
                max(previous.skip, insertion.skip),
            )
        else:
            joined.append(insertion)
    return joined


def _applied(text: str, insertions: Sequence[_Insertion]) -> str:
    pieces: List[str] = []
    cursor = 0
    for insertion in insertions:
        if insertion.offset > cursor:
            pieces.append(text[cursor : insertion.offset])
        pieces.append(insertion.text)
        cursor = max(cursor, insertion.offset + insertion.skip)
    pieces.append(text[cursor:])
    return "".join(pieces)


def _comment_lines(text: str) -> Optional[List[int]]:
    """The line of each comment of ``text``, in order; None when it does not tokenize."""
    try:
        return [token.start[0] for token in _tokens(text) if token.kind == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return None


def _helper_in(tree: ast.Module, position: int) -> Optional[ast.AST]:
    if position < len(tree.body) and isinstance(
        tree.body[position], (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        return tree.body[position]
    return None


@dataclass(frozen=True)
class _DirectiveCheck:
    """A directive the formatter must leave beside its code: its comment and that code's ends."""

    comment: int
    anchor: NodePath
    line_start: Optional[NodePath]


@dataclass(frozen=True)
class WovenHelper:
    """A helper's unparsed text with its comments written in, and how to check a formatting of it."""

    text: str
    position: int
    body_offset: int
    comment_count: int
    checks: Tuple[_DirectiveCheck, ...] = ()

    def keeps_directives(self, formatted: str) -> bool:
        """Whether ``formatted`` leaves every directive on the line of the code it was beside.

        The code a directive covered, from the first node its line started
        to the node it followed, must still end and start on the directive's
        line: a formatter that split that line under the comment, or moved
        the comment past a closing bracket, changed what the directive
        covers.
        """
        if not self.checks:
            return True
        try:
            tree = ast.parse(formatted)
        except SyntaxError:
            return False
        helper = _helper_in(tree, self.position)
        lines = _comment_lines(formatted)
        if helper is None or lines is None or len(lines) != self.comment_count:
            return False
        statements = getattr(helper, "body")[self.body_offset :]
        for check in self.checks:
            line = lines[check.comment]
            anchor, _, _ = _resolve(statements, check.anchor)
            if getattr(anchor, "end_lineno", line) != line:
                return False
            if check.line_start is not None:
                start, _, _ = _resolve(statements, check.line_start)
                if getattr(start, "lineno", line) != line:
                    return False
        return True


def weave_comments(node: ast.AST, helper: ast.AST, comments: HelperComments) -> WovenHelper:
    """``ast.unparse(node)`` with ``comments`` written where they stood in the helper's body.

    ``node`` is the helper itself, or a module whose last statement is the
    helper. The result has the syntax tree of the plain rendering, which is
    checked; where a comment cannot stand where it was written, it goes to
    its statement's line instead. Raises ``CommentPlacementError`` when even
    that does not hold.
    """
    text = ast.unparse(node)
    tree = ast.parse(text)
    position = len(tree.body) - 1 if isinstance(node, ast.Module) else 0
    rendered = _helper_in(tree, position)
    if rendered is None or canonical_dump(rendered) != canonical_dump(helper):
        raise CommentPlacementError("The helper does not render to itself")
    statements = getattr(rendered, "body")[comments.body_offset :]
    layout = _Layout(text)
    expected = canonical_dump(tree)
    for safe in (False, True):
        insertions = _joined(_Weaver(layout, statements, comments.comments).weave(safe))
        woven = _applied(text, insertions)
        written = [insertion for insertion in insertions if insertion.comments]
        lines = _comment_lines(woven)
        try:
            same = canonical_dump(ast.parse(woven)) == expected
        except SyntaxError:
            same = False
        if same and lines is not None and len(lines) == len(written):
            ordinal_to_comment = {
                ordinal: index
                for index, insertion in enumerate(written)
                for ordinal in insertion.comments
            }
            checks = tuple(
                _DirectiveCheck(
                    ordinal_to_comment[ordinal], comment.anchor.node, comment.line_start
                )
                for ordinal, comment in enumerate(comments.comments)
                if comment.directive
                and not comment.anchor.own_line
                and comment.anchor.placement is Placement.TRAILING
            )
            return WovenHelper(woven, position, comments.body_offset, len(written), checks)
    raise CommentPlacementError("The helper's comments cannot be written into its text")
