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

"""The errors a project's type check already reports, and what a later check adds to them.

Typed mode used to refuse a project whose check, as Towel runs it, reported
any error. That check is seldom the project's own: of 20 corpus projects, all
17 that type-check pass their own check as their CI runs it, while Towel's
was clean for 6, its errors lying in tests, benchmarks and docs the CI never
checks, from checkers no CI step runs, and without the CI's flags. So a run
now compares. The errors the project's check reports before a change are the
reference, and a change is rejected for any error the reference does not
account for (:meth:`KnownErrors.introduced`).

Whether an error after a change is one the reference had is a question about
two checks of two different texts, and the line number is the obvious answer
and the wrong one: a helper inserted above an error moves it, so a comparison
by line would reject every change above any pre-existing error. A line the
change left alone keeps its text, though, only elsewhere. So a file the change
and the run have left alone is compared by file, line and message. In a file
whose text has changed, the two texts are aligned by a line diff in which the
lines the change replaced with calls, and the helper it wrote, pair with
nothing, and each error after the change must be accounted for by one error of
the reference that stood where it stands:

- on a line the change left alone, by the reference's error on that same line,
  wherever the line now stands;
- in the helper's body, by the error with the same message at the same
  statement of one copy of the block the helper was made from, statements
  being counted in order through the block and through the helper's body, so
  that a helper is compared with its block however the formatter laid either
  out. One copy's errors account for the helper's, each once: merging two
  copies into one helper frees the other copy's errors, and they account for
  nothing there;
- anywhere else the change wrote -- a call site, an import -- by an error with
  the same message on the lines that the same stretch of the diff replaced,
  which for a call site is its own copy and whatever else the diff took with
  it.

Every other error is new. Where either text of a changed file is unknown
nothing can be aligned, and every error in it is new. A pyright message
carries its rule, so two errors differ when their rules do; Towel's mypy
worker hides error codes, so two mypy errors differ when their texts do.

What this can mistake, and which way it errs:

- A message that embeds a line (mypy's ``Name "x" already defined on line
  12``) changes when that line moves, so it reappears as new and the change is
  rejected. It fails closed: a shift can cost a change, never hide an error.
- A helper whose statements do not follow its block's one for one (a
  statement Towel added before the block's, a block whose statements the
  helper does not keep) is accounted for by no copy, so any error in it is
  new; statements the helper adds after the block's, the ``return`` of the
  values the block leaves behind, have no counterpart and are new too.
- An error of the reference accounts for one error after a change, never
  more: two identical errors where there was one is one new, and when a
  stretch holds more of a message than it replaced, every one of them is
  reported, since nothing says which is the new one.
- The reference follows the project. Once a change is written, its own check
  is what the next change is compared with, so an error one change removed
  cannot be spent by another that brings it back: against the original's
  errors, the second would pass.
- The alignment is a line diff, so it holds whatever wrote the lines,
  formatter and import sorter included. An error on an import the sorter moved
  to another stretch of the file is new, which costs a change.

A name the checker cannot type -- an import it cannot resolve or finds no
types for, a decorator without types -- is ``Any`` to it wherever it goes, and
``Any`` accepts everything, so a change that misuses what that name carries is
invisible to the check. Such errors are named by :func:`makes_names_any`, and
the files they lie in by :func:`files_where_names_are_any`, for the run to
decline changing them. Since a configuration can hide those errors
(``ignore_missing_imports``), the checker is also asked directly what each
import binds (:func:`import_probes`, :func:`imports_typed_as_any`).
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field, replace
import difflib
import functools
import os
from pathlib import Path
import re
from types import MappingProxyType
import warnings
from typing import (
    Callable,
    Collection,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .reachability import probe_plan
from .type_inference import RevealRequest, TypeDiagnostic

__all__ = [
    "IMPORT_PROBE_PREFIX",
    "NO_SHAPE",
    "ChangeShape",
    "CheckedChange",
    "KnownErrors",
    "LineMap",
    "ReplacedCopy",
    "ImportProbes",
    "ImportQuestion",
    "files_where_names_are_any",
    "import_probes",
    "imports_typed_as_any",
    "makes_names_any",
    "names_any_warning",
    "pre_existing_summary",
    "resolved_path",
    "unchanged_lines",
    "unlooked_warning",
]


def resolved_path(path: str) -> str:
    """``path`` as a comparison needs it: absolute, with every symbolic link resolved."""
    return os.path.realpath(path)


def _no_text(path: str) -> Optional[str]:
    return None


@dataclass(frozen=True)
class LineMap:
    """Where each line a change left alone stands after it, by one-based line number."""

    after_of: Mapping[int, int]
    """Each unchanged line's number before the change to its number after."""
    unchanged_after: FrozenSet[int]
    """The lines after the change that it left alone; every other line it wrote."""


@functools.lru_cache(maxsize=32)
def unchanged_lines(before: str, after: str) -> LineMap:
    """The lines of ``before`` that ``after`` keeps, as a line diff of the two texts pairs them.

    Lines are split on LF alone, as the tokenizer counts them in decoded
    source (``towel.source_text.source_lines``). The diff runs without its
    junk heuristic, which would leave repeated lines -- blank ones, ``pass``
    -- unpaired: about 20 ms for a 3,400-line module. Pure in its arguments,
    so a check that is compared once per checker aligns each file once.
    """
    old, new = before.split("\n"), after.split("\n")
    after_of: Dict[int, int] = {}
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, start, end, target, _ in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(end - start):
                after_of[start + offset + 1] = target + offset + 1
    return LineMap(MappingProxyType(after_of), frozenset(after_of.values()))


@dataclass(frozen=True)
class ReplacedCopy:
    """One duplicate a change replaced with a call: where it stood, in the text it stood in.

    ``path`` is resolved, in the reference's project, and ``first`` and
    ``last`` number the lines of ``text``, the file as the change found it.
    """

    path: str
    first: int
    last: int
    text: str


@dataclass(frozen=True)
class ChangeShape:
    """What a change wrote where: the copies it replaced, and the helper it wrote them into.

    ``helper_path`` (resolved) and ``helper_name`` locate the helper in the
    text after the change; a change that calls a function already there has
    none. Told nothing of a change's shape, a comparison knows no helper and
    no copy: their lines align as any others do, a line pairing with another
    only where the two read the same.
    """

    copies: Tuple[ReplacedCopy, ...] = ()
    helper_path: Optional[str] = None
    helper_name: Optional[str] = None


NO_SHAPE = ChangeShape()
"""The shape of a change nothing is known about: an earlier change, a whole run."""


@dataclass(frozen=True)
class _Statement:
    """A statement's kind and the lines it spans, decorators included."""

    kind: str
    first: int
    last: int


def _statement_lists(node: ast.AST) -> List[List[ast.stmt]]:
    lists: List[List[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        value = getattr(node, name, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            lists.append(value)
    lists.extend(handler.body for handler in getattr(node, "handlers", ()))
    lists.extend(case.body for case in getattr(node, "cases", ()))
    return lists


def _in_order(statements: Sequence[ast.stmt]) -> List[_Statement]:
    """``statements`` and every statement inside them, each before the ones it holds."""
    found: List[_Statement] = []
    for node in statements:
        decorators: Sequence[ast.expr] = getattr(node, "decorator_list", ())
        first = min([node.lineno, *(decorator.lineno for decorator in decorators)])
        found.append(_Statement(type(node).__name__, first, node.end_lineno or node.lineno))
        for inner in _statement_lists(node):
            found.extend(_in_order(inner))
    return found


@functools.lru_cache(maxsize=32)
def _parsed(text: str) -> Optional[ast.Module]:
    """``text`` parsed, or None; the change's texts compiled, so this fails only for a stranger's."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


@functools.lru_cache(maxsize=64)
def _block_statements(text: str, first: int, last: int) -> Tuple[_Statement, ...]:
    """The statements of ``text`` that lie within lines ``first``-``last``, in order."""
    tree = _parsed(text)
    if tree is None:
        return ()
    return tuple(s for s in _in_order(tree.body) if s.first >= first and s.last <= last)


@dataclass(frozen=True)
class _WrittenHelper:
    """Where the helper stands after the change, and its body's statements in order."""

    first: int
    last: int
    statements: Tuple[_Statement, ...]


@functools.lru_cache(maxsize=32)
def _written_helper(text: str, name: str) -> Optional[_WrittenHelper]:
    """The one definition of ``name`` in ``text``; None when there is not exactly one.

    Its body's ``global`` and ``nonlocal`` declarations come first and are
    Towel's, not the block's, so they are not among the statements.
    """
    tree = _parsed(text)
    if tree is None:
        return None
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(found) != 1:
        return None
    helper = found[0]
    body = list(helper.body)
    while body and isinstance(body[0], (ast.Global, ast.Nonlocal)):
        body.pop(0)
    first = min([helper.lineno, *(decorator.lineno for decorator in helper.decorator_list)])
    return _WrittenHelper(first, helper.end_lineno or helper.lineno, tuple(_in_order(body)))


def _statement_at(statements: Sequence[_Statement], line: int) -> Optional[int]:
    """The index of the innermost of ``statements`` that holds ``line``: the last, in order."""
    found: Optional[int] = None
    for index, statement in enumerate(statements):
        if statement.first <= line <= statement.last:
            found = index
    return found


@dataclass(frozen=True)
class _Alignment:
    """Two texts of one file, paired line by line by a diff.

    ``after_of`` maps each line the change left alone to where it now
    stands. Every other line lies in a stretch (a hunk) of the diff, numbered
    the same on both sides: ``hunk_before`` for the lines the change replaced
    or removed, ``hunk_after`` for the lines it wrote.
    """

    after_of: Mapping[int, int]
    hunk_before: Mapping[int, int]
    hunk_after: Mapping[int, int]
    lines_before: int
    lines_after: int


@functools.lru_cache(maxsize=32)
def _aligned(
    before: str, after: str, replaced: FrozenSet[int], written: FrozenSet[int]
) -> _Alignment:
    """``before`` and ``after`` aligned, with the ``replaced`` and ``written`` lines paired to nothing.

    The copies a change replaced and the helper it wrote are known, so no
    coincidence of text may pair a line of the helper with a line of a copy:
    each masked line is a value no line of the other text equals. The diff
    runs without its junk heuristic, as :func:`unchanged_lines` does.
    """
    old: List[object] = [
        ("replaced", number) if number in replaced else line
        for number, line in enumerate(before.split("\n"), 1)
    ]
    new: List[object] = [
        ("written", number) if number in written else line
        for number, line in enumerate(after.split("\n"), 1)
    ]
    after_of: Dict[int, int] = {}
    hunk_before: Dict[int, int] = {}
    hunk_after: Dict[int, int] = {}
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for hunk, (tag, start, end, target, target_end) in enumerate(matcher.get_opcodes()):
        if tag == "equal":
            for offset in range(end - start):
                after_of[start + offset + 1] = target + offset + 1
            continue
        for line in range(start + 1, end + 1):
            hunk_before[line] = hunk
        for line in range(target + 1, target_end + 1):
            hunk_after[line] = hunk
    return _Alignment(
        MappingProxyType(after_of),
        MappingProxyType(hunk_before),
        MappingProxyType(hunk_after),
        len(old),
        len(new),
    )


_Place = Tuple[str, Optional[int], str]
"""An error's file, line and message: what a file no change has touched is compared by."""

_Pool = Tuple[str, int]
"""A stretch of the diff: its file and its number there."""

_UNPLACED = -1
"""The stretch of a file that holds the errors that name no line of it."""


@dataclass(frozen=True)
class KnownErrors:
    """The errors a check of the project reported, and which files may have changed since.

    ``errors`` carry resolved paths in the project that check was made of.
    ``moved`` names, the same way, the files whose text a change accepted
    since then may have altered, so that a line in them no longer means what
    it meant to that check. ``texts`` holds, for each file with an error that
    a change may touch, the text the check saw, against which a changed file
    is aligned.
    """

    errors: Tuple[TypeDiagnostic, ...] = ()
    moved: FrozenSet[str] = frozenset()
    texts: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def of(
        cls,
        errors: Iterable[TypeDiagnostic],
        where: Callable[[str], str] = resolved_path,
        texts: Mapping[str, str] = MappingProxyType({}),
    ) -> "KnownErrors":
        """What a check reported, its paths as ``where`` places them in the reference's project.

        ``texts`` are the checked texts by resolved path; only those of files
        with an error are kept.
        """
        placed = tuple(TypeDiagnostic(where(e.path), e.message, e.line) for e in errors)
        erring = {error.path for error in placed}
        kept = {path: text for path, text in texts.items() if path in erring}
        return cls(placed, texts=MappingProxyType(kept))

    def moving(self, paths: Iterable[str]) -> "KnownErrors":
        """These errors, with ``paths`` (resolved) among the files whose lines may have moved."""
        return replace(self, moved=self.moved | frozenset(paths))

    def introduced(
        self,
        after: Sequence[TypeDiagnostic],
        changing: Collection[str] = (),
        *,
        where: Callable[[str], str] = resolved_path,
        texts_after: Callable[[str], Optional[str]] = _no_text,
        shape: ChangeShape = NO_SHAPE,
    ) -> Tuple[TypeDiagnostic, ...]:
        """The errors of ``after`` these do not account for, as ``after`` reported them.

        ``after`` is a check of the project with a change made to ``changing``
        (resolved paths), whose ``shape`` says which copies it replaced and
        where it wrote the helper; ``where`` places each of its paths in the
        reference's project, since a run that refactors a private copy is
        checked there, and ``texts_after`` gives, by resolved path, the text
        that check saw of a file whose lines may have moved. In a file neither
        the change nor an earlier one has touched, an error must match one of
        these at the same line. In any other it must be accounted for by one
        of these that stood where it stands (the module docstring says how),
        and each error of the reference accounts for one error after, never
        more.
        """
        shifted = self.moved | frozenset(changing)
        placed: Counter[_Place] = Counter(
            (error.path, error.line, error.message)
            for error in self.errors
            if error.path not in shifted
        )
        new: List[int] = []
        in_shifted: Dict[str, List[int]] = {}
        for index, error in enumerate(after):
            path = where(error.path)
            if path in shifted:
                in_shifted.setdefault(path, []).append(index)
                continue
            place = (path, error.line, error.message)
            if placed[place]:
                placed[place] -= 1
            else:
                new.append(index)
        if in_shifted:
            new.extend(_ChangedFiles(self, shifted, texts_after, shape).new(after, in_shifted))
        return tuple(after[index] for index in sorted(new))


class _ChangedFiles:
    """The reference's errors in the files a change may have moved, pooled where each one stood.

    Built once per comparison and discarded with it. Each error of the
    reference is in exactly one place: on a line the change left alone, where
    only an error on that line after it can spend it; in a copy the change
    replaced, where the helper can spend it, or the call site that took the
    copy's place; or in another stretch of the diff, where only an error in
    that stretch can.
    """

    def __init__(
        self,
        known: KnownErrors,
        shifted: FrozenSet[str],
        texts_after: Callable[[str], Optional[str]],
        shape: ChangeShape,
    ) -> None:
        self._copies = [copy for copy in shape.copies if known.texts.get(copy.path) == copy.text]
        self._alignments: Dict[str, Optional[_Alignment]] = {}
        self._helper: Optional[_WrittenHelper] = None
        self._helper_path = shape.helper_path
        helper_text = texts_after(shape.helper_path) if shape.helper_path is not None else None
        if helper_text is not None and shape.helper_name is not None:
            self._helper = _written_helper(helper_text, shape.helper_name)
        for path in shifted:
            before, now = known.texts.get(path), texts_after(path)
            if before is None or now is None:
                self._alignments[path] = None  # nothing can be aligned: every error is new
                continue
            replaced = frozenset(
                line
                for copy in self._copies
                if copy.path == path
                for line in range(copy.first, copy.last + 1)
            )
            written: FrozenSet[int] = frozenset()
            if self._helper is not None and path == self._helper_path:
                written = frozenset(range(self._helper.first, self._helper.last + 1))
            self._alignments[path] = _aligned(before, now, replaced, written)
        # Each reference error, by its index, placed where it stood.
        self._kept: Counter[Tuple[str, int, str]] = Counter()
        self._pooled: Dict[_Pool, List[int]] = {}
        self._in_copy: Dict[int, List[int]] = {}
        self._errors = known.errors
        for index, error in enumerate(known.errors):
            if error.path not in shifted:
                continue
            alignment = self._alignments.get(error.path)
            if alignment is None:
                continue
            line = error.line
            if line is None or not 1 <= line <= alignment.lines_before:
                self._pooled.setdefault((error.path, _UNPLACED), []).append(index)
                continue
            moved_to = alignment.after_of.get(line)
            if moved_to is not None:
                self._kept[(error.path, moved_to, error.message)] += 1
                continue
            self._pooled.setdefault((error.path, alignment.hunk_before[line]), []).append(index)
            for number, copy in enumerate(self._copies):
                if copy.path == error.path and copy.first <= line <= copy.last:
                    self._in_copy.setdefault(number, []).append(index)

    def new(
        self, after: Sequence[TypeDiagnostic], in_shifted: Mapping[str, List[int]]
    ) -> List[int]:
        """Which of ``after``, at the ``in_shifted`` indices by file, nothing here accounts for.

        The helper's errors are compared with each copy in turn that it
        follows statement for statement (:meth:`_offered_by`), and with none;
        whichever leaves the fewest errors unaccounted for, the helper's and
        the rest of the change's together, is the answer, so that a copy's
        errors spent on the helper are the ones its call site does not need.
        """
        settled: List[int] = []
        in_helper: List[int] = []
        by_pool: Dict[_Pool, List[int]] = {}
        for path, indices in in_shifted.items():
            alignment = self._alignments.get(path)
            if alignment is None:
                settled.extend(indices)
                continue
            for index in indices:
                line = after[index].line
                if line is None or not 1 <= line <= alignment.lines_after:
                    by_pool.setdefault((path, _UNPLACED), []).append(index)
                elif line in alignment.hunk_after:
                    if self._in_the_helper(path, line):
                        in_helper.append(index)
                    else:
                        by_pool.setdefault((path, alignment.hunk_after[line]), []).append(index)
                else:
                    place = (path, line, after[index].message)
                    if self._kept[place]:
                        self._kept[place] -= 1
                    else:
                        settled.append(index)
        wanted: Dict[Tuple[Optional[int], str], List[int]] = {}
        helper = self._helper
        for index in in_helper:
            line = after[index].line
            at = _statement_at(helper.statements, line) if helper and line is not None else None
            wanted.setdefault((at, after[index].message), []).append(index)
        offers = [self._offered_by(number) for number in range(len(self._copies))]
        choices = [offered for offered in offers if offered is not None]
        unaccounted = [
            self._unaccounted(after, wanted, by_pool, offered) for offered in [*choices, {}]
        ]
        return settled + min(unaccounted, key=len)

    def _in_the_helper(self, path: str, line: int) -> bool:
        helper = self._helper
        return (
            helper is not None and path == self._helper_path and helper.first <= line <= helper.last
        )

    def _offered_by(self, number: int) -> Optional[Dict[Tuple[int, str], List[int]]]:
        """The errors of copy ``number``, by statement and message; None if the helper does not follow it.

        The helper's ``i``-th statement stands for the copy's ``i``-th,
        counted in order through the copy's lines and the helper's body, when
        the kinds of the copy's statements are those the helper's body begins
        with; statements the helper adds after them stand for none.
        """
        helper, copy = self._helper, self._copies[number]
        if helper is None:
            return None
        statements = _block_statements(copy.text, copy.first, copy.last)
        kinds = [statement.kind for statement in statements]
        if not kinds or kinds != [s.kind for s in helper.statements[: len(kinds)]]:
            return None
        offered: Dict[Tuple[int, str], List[int]] = {}
        for index in self._in_copy.get(number, ()):
            error = self._errors[index]
            at = _statement_at(statements, error.line) if error.line is not None else None
            if at is not None:
                offered.setdefault((at, error.message), []).append(index)
        return offered

    def _unaccounted(
        self,
        after: Sequence[TypeDiagnostic],
        wanted: Mapping[Tuple[Optional[int], str], List[int]],
        by_pool: Mapping[_Pool, List[int]],
        offered: Mapping[Tuple[int, str], List[int]],
    ) -> List[int]:
        """The errors of the helper and of the change's stretches that nothing accounts for.

        The helper's errors, by statement and message in ``wanted``, are
        accounted for by one copy's, ``offered``, each once; a statement that
        holds more of a message than the copy's does is new in every
        instance. The copy's errors so spent account for nothing else, and
        each stretch's errors are compared by message with the rest of the
        reference's errors in the same stretch.
        """
        new: List[int] = []
        spent: Set[int] = set()
        for (statement, message), found in wanted.items():
            available = offered.get((statement, message), []) if statement is not None else []
            if len(found) <= len(available):
                spent.update(available[: len(found)])
            else:
                new.extend(found)
        for pool, indices in by_pool.items():
            available_messages = Counter(
                self._errors[index].message
                for index in self._pooled.get(pool, ())
                if index not in spent
            )
            found_messages = Counter(after[index].message for index in indices)
            new.extend(
                index
                for index in indices
                if found_messages[after[index].message] > available_messages[after[index].message]
            )
        return new


@dataclass(frozen=True)
class CheckedChange:
    """A change the checker accepted, and what its check of the project with it reported.

    ``files`` are the change's texts at the paths it was checked at. Once they
    are what those files hold, ``reported`` is what the project reports.
    """

    files: Tuple[Tuple[str, str], ...]
    reported: KnownErrors


_NAMES_ANY = re.compile(
    # mypy, by error code when it shows them ...
    r"\[(?:import-not-found|import-untyped|import|untyped-decorator)\]\s*$"
    # ... and by the words, when a configuration hides the codes.
    r"|Cannot find implementation or library stub for module named "
    r"|Library stubs not installed for "
    r"|module is installed, but missing library stubs or py\.typed marker"
    r"|Untyped decorator makes function .* untyped"
    r'|Class cannot subclass .* \(has type "Any"\)'
    # pyright, whose messages Towel prefixes with the rule.
    r"|^pyright: (?:reportMissingImports|reportMissingTypeStubs|reportUntypedBaseClass): "
)


def makes_names_any(error: TypeDiagnostic) -> bool:
    """Whether ``error`` leaves a name the checker treats as ``Any`` wherever it goes.

    An import mypy cannot resolve or finds no types for (``import-not-found``,
    ``import-untyped``), one pyright cannot resolve or finds no stubs for, a
    decorator without types, a base class the checker cannot type. Everything
    such a name reaches is ``Any``, which accepts every use, so a change that
    misuses it is one the check cannot see.
    """
    return _NAMES_ANY.search(error.message) is not None


def files_where_names_are_any(
    errors: Iterable[TypeDiagnostic],
) -> Mapping[str, Tuple[TypeDiagnostic, ...]]:
    """Each file (resolved) holding an error that :func:`makes_names_any`, with those errors."""
    found: Dict[str, List[TypeDiagnostic]] = {}
    for error in errors:
        if makes_names_any(error):
            found.setdefault(resolved_path(error.path), []).append(error)
    return {path: tuple(listed) for path, listed in found.items()}


IMPORT_PROBE_PREFIX = "_towel_imported_"
"""How the names a probe binds what an import binds to begin, in the probed text only."""

_IMPORTED = IMPORT_PROBE_PREFIX + "{}"

_ATTRIBUTES_PROBED = 3
"""How many of the attributes a module is read for are asked about, per import."""


@dataclass(frozen=True)
class ImportQuestion:
    """One question about what an import binds, and what its answer means.

    ``key`` is where its answer comes back (``towel.type_inference.RevealKey``)
    and ``line`` the import's own line. mypy answers a ``module`` question
    ``Any`` for a module it cannot resolve or finds no types for; pyright
    answers a ``name`` or ``attribute`` question ``Unknown`` for what an
    import it cannot resolve binds. Neither answers that way for a module it
    sees, and a checker that does not look at the import answers nothing.
    """

    key: Tuple[str, int, int]
    line: int
    kind: str
    subject: str


@dataclass(frozen=True)
class ImportProbes:
    """The reveal requests that ask a checker about every import of one file, and what each asks."""

    requests: Tuple[RevealRequest, ...]
    questions: Tuple[ImportQuestion, ...]


@dataclass(frozen=True)
class _Probe:
    """An import the probed text adds, the import it stands for, and what it asks."""

    statement: str
    line: int
    asked: Tuple[Tuple[str, str, str], ...]
    """(expression revealed, kind of question, what it is about)"""


def _module_attributes(tree: ast.Module, bound: str, within: Sequence[str]) -> List[str]:
    """The attributes read from a module bound to ``bound``, past ``within``: a few, in order."""
    found: Dict[str, None] = {}
    for node in ast.walk(tree):
        chain: List[str] = []
        inner: ast.expr = node if isinstance(node, ast.Attribute) else ast.Constant(None)
        while isinstance(inner, ast.Attribute):
            chain.append(inner.attr)
            inner = inner.value
        path = chain[::-1]
        if isinstance(inner, ast.Name) and inner.id == bound and len(path) > len(within):
            if path[: len(within)] == list(within):
                found.setdefault(path[len(within)], None)
                if len(found) == _ATTRIBUTES_PROBED:
                    break
    return list(found)


def _probes_of(node: ast.stmt, tree: ast.Module, first: int) -> List[_Probe]:
    """What the probed text adds for the import statement ``node``, its aliases numbered from ``first``."""
    probes: List[_Probe] = []
    number = first
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = _IMPORTED.format(number)
            number += 1
            dotted = alias.name.split(".")
            bound, within = (alias.asname, []) if alias.asname else (dotted[0], dotted[1:])
            asked = [(name, "module", f'module "{alias.name}"')]
            asked += [
                (f"{name}.{attribute}", "attribute", f'"{attribute}" of module "{alias.name}"')
                for attribute in _module_attributes(tree, bound, within)
            ]
            probes.append(_Probe(f"import {alias.name} as {name}", node.lineno, tuple(asked)))
        return probes
    if not isinstance(node, ast.ImportFrom):
        return probes
    dots = "." * node.level
    spelled = f"{dots}{node.module or ''}"
    if node.module is not None:
        name = _IMPORTED.format(number)
        number += 1
        head, _, last = node.module.rpartition(".")
        statement = (
            f"from {dots}{head} import {last} as {name}"
            if node.level
            else f"import {node.module} as {name}"
        )
        probes.append(_Probe(statement, node.lineno, ((name, "module", f'module "{spelled}"'),)))
    for alias in node.names:
        if alias.name == "*":
            continue
        name = _IMPORTED.format(number)
        number += 1
        subject = f'"{alias.name}" imported from "{spelled}"'
        statement = f"from {spelled} import {alias.name} as {name}"
        probes.append(_Probe(statement, node.lineno, ((name, "name", subject),)))
    return probes


def import_probes(path: str, text: str) -> Optional[ImportProbes]:
    """Requests that reveal, for each import in ``text``, what it binds, where the import stands.

    Before each import statement the probed text imports the same module, and
    each name the statement imports, under names of its own, and reveals them:
    ``import m as _towel_imported_0`` then ``reveal_type(_towel_imported_0)``.
    So a module the checker cannot resolve, or finds no types for, is found
    whether or not the configuration has it report that
    (``ignore_missing_imports``, pyright's ``reportMissingImports``) and
    whether or not the import carries ``# type: ignore``, and only where the
    checker looks at the import: one the platform or Python of the check never
    reaches is asked about in the same place, and answered nothing. A module
    imported whole is also asked about the attributes the file reads from it,
    since pyright gives a module it cannot resolve a module's type. ``None``
    when ``text`` has no import to ask about or no probe can be placed in it.
    """
    plan = probe_plan(text)
    tree = _parsed(text)
    if plan is None or tree is None:
        return None
    lines = text.split("\n")
    added: Dict[Tuple[int, str], List[_Probe]] = {}
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        encoded = lines[node.lineno - 1].encode("utf-8")
        place = (node.lineno, len(encoded[: node.col_offset].decode("utf-8", "replace")))
        site = plan.sites.get(place)
        if site is None:
            continue
        probes = _probes_of(node, tree, count)
        count += len(probes)
        added.setdefault(site, []).extend(probes)
    if not added:
        return None
    probed = plan.text.split("\n")
    landed: Dict[Tuple[int, str], int] = {}
    shift = 0
    for site, probes in sorted(added.items()):
        at = site[0] - 1 + shift
        probed[at:at] = [f"{site[1]}{probe.statement}" for probe in probes]
        shift += len(probes)
        landed[site] = site[0] + shift
    source = "\n".join(probed)
    requests: List[RevealRequest] = []
    questions: List[ImportQuestion] = []
    for site, probes in sorted(added.items()):
        asked = [(probe.line, question) for probe in probes for question in probe.asked]
        expressions = tuple(expression for _, (expression, _, _) in asked)
        requests.append(RevealRequest(path, source, landed[site], site[1], expressions))
        questions += [
            ImportQuestion((path, landed[site], index), line, kind, subject)
            for index, (line, (_, kind, subject)) in enumerate(asked)
        ]
    return ImportProbes(tuple(requests), tuple(questions))


def imports_typed_as_any(
    probes: ImportProbes, answers: Sequence[Mapping[Tuple[str, int, int], str]], where: str
) -> Tuple[TypeDiagnostic, ...]:
    """What the ``answers`` (one mapping per checker) say the checker cannot see, at ``where``.

    Each finding is stated as an error of the file it lies in (``where``,
    resolved), at the import's line, so that it is reported and declined with
    the errors of :func:`makes_names_any`.
    """
    found: Dict[Tuple[int, str], TypeDiagnostic] = {}
    for question in probes.questions:
        for answer in answers:
            revealed = answer.get(question.key)
            blind = (question.kind == "module" and revealed == "Any") or (
                question.kind != "module" and revealed == "Unknown"
            )
            if blind and (question.line, question.subject) not in found:
                found[(question.line, question.subject)] = TypeDiagnostic(
                    where,
                    f"the type checker types {question.subject} as {revealed}: it cannot "
                    "resolve the import, or finds no types for it",
                    question.line,
                )
    return tuple(found.values())


_FILES_SHOWN = 5
"""How many files, or errors, a report lists before it counts the rest."""


def _shown(path: str, root: Path) -> str:
    """``path`` relative to ``root`` where it lies inside it, as a user reads it."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def pre_existing_summary(errors: Sequence[TypeDiagnostic], root: Path) -> str:
    """What the original check reports, file by file, and what the run will do about it."""
    root = Path(resolved_path(str(root)))
    by_file = Counter(_shown(error.path, root) for error in errors)
    lines = [
        f"The original project's type check reports {len(errors)} error(s) in "
        f"{len(by_file)} file(s). The run leaves them as they are and rejects a change "
        "only for an error they do not account for (TOWEL_DEBUG_TYPES=1 lists them):"
    ]
    lines += [f"  {name}: {count}" for name, count in by_file.most_common(_FILES_SHOWN)]
    if len(by_file) > _FILES_SHOWN:
        lines.append(f"  ... and {len(by_file) - _FILES_SHOWN} more file(s)")
    return "\n".join(lines)


def names_any_warning(
    names_any: Mapping[str, Tuple[TypeDiagnostic, ...]],
    root: Path,
    changed: Collection[str],
) -> Optional[str]:
    """Where the original check leaves names it cannot type, and what the run does there.

    ``names_any`` is :func:`files_where_names_are_any` of the original's
    errors, with what :func:`imports_typed_as_any` found, and ``changed`` the
    files (resolved) the run may change: no change
    to one of those that holds such a name is attempted. The rest are named
    too, since where they use such a name, a change is checked against
    ``Any``, which accepts it. ``None`` when there is nothing to say.
    """
    if not names_any:
        return None
    root = Path(resolved_path(str(root)))
    declined = [path for path in names_any if path in changed]
    elsewhere = [path for path in names_any if path not in changed]
    count = sum(len(errors) for errors in names_any.values())
    lines = [
        f"warning: {count} import(s) or error(s) leave a name the checker cannot type (an "
        "import it cannot resolve or finds no types for, or a decorator without types), "
        "and whatever such a name reaches is Any to the checker, which accepts any use "
        "of it, so a new error there would go unseen."
    ]
    if declined:
        lines.append(
            f"No change to these {len(declined)} file(s) is attempted; install what they "
            "import, and its stubs, where Towel runs to have them refactored with types:"
        )
        listed = [(path, error) for path in declined for error in names_any[path]]
        lines += [
            f"  {_shown(path, root)}:{error.line or '?'}: {error.message}"
            for path, error in listed[:_FILES_SHOWN]
        ]
        if len(listed) > _FILES_SHOWN:
            lines.append(f"  ... and {len(listed) - _FILES_SHOWN} more")
    if elsewhere:
        shown = ", ".join(_shown(path, root) for path in elsewhere[:_FILES_SHOWN])
        more = f" and {len(elsewhere) - _FILES_SHOWN} more" if len(elsewhere) > _FILES_SHOWN else ""
        lines.append(
            f"{'The rest lie' if declined else 'They lie'} in {len(elsewhere)} file(s) the run "
            f"does not change ({shown}{more}), where a use of such a name is checked against Any."
        )
    return "\n".join(lines)


def unlooked_warning(regions: Mapping[str, Sequence[Tuple[int, int]]], root: Path) -> str:
    """The regions of the analyzed files the checker does not look at, and what the run does there."""
    root = Path(resolved_path(str(root)))
    listed = [(path, start, end) for path, spans in sorted(regions.items()) for start, end in spans]
    lines = [
        f"warning: the type checker does not look at {len(listed)} region(s) of the code this "
        "run may change: it takes them to be unreachable on the platform and Python it checks "
        "for (a sys.platform, sys.version_info or TYPE_CHECKING test it makes false, an assert "
        "it knows fails), so it reports nothing there and a check says nothing about a change. "
        "No change to them is attempted; the project's own check may look at them on another "
        "platform or Python:"
    ]
    lines += [f"  {_shown(path, root)}:{start}-{end}" for path, start, end in listed[:_FILES_SHOWN]]
    if len(listed) > _FILES_SHOWN:
        lines.append(f"  ... and {len(listed) - _FILES_SHOWN} more")
    return "\n".join(lines)
