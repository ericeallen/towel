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
reference, and only an error the reference does not account for is the
change's (:meth:`KnownErrors.introduced`).

Whether an error after a change is one the reference had is a question about
two checks of two different texts, and the line number is the obvious answer
and the wrong one: a helper inserted above an error moves it, so a comparison
by line would reject every change above any pre-existing error. A line the
change left alone keeps its text, though, only elsewhere. So a file the change
and the run have left alone is compared by file, line and message. In a file
whose text has changed, the two texts are aligned (:func:`unchanged_lines`),
and an error on a line the change left alone must match the reference's error
on that same line, wherever it now stands; only the errors on lines the change
wrote (the helper, the call sites, an import) are compared by message, as a
multiset, with the reference's errors on the lines it replaced. Where either
text is unknown, the whole file is compared by message. A pyright message
carries its rule, so two errors differ when their rules do; Towel's mypy worker
hides error codes, so two mypy errors differ when their texts do.

What this can mistake, and which way it errs:

- A message that embeds a line (mypy's ``Name "x" already defined on line
  12``) changes when that line moves, so it reappears as new and the change is
  rejected. It fails closed: a shift can cost a change, never hide an error.
- On the lines a change wrote, an error that disappears from a line it
  replaced where another with the very same message appears is taken to have
  moved, as a duplicated block's error does when the block moves into the
  helper: it is the same error, and it was already there. An error that
  disappears from a line the change left alone cannot have moved, since its
  code did not, and accounts for nothing the change wrote.
- An error of the reference accounts for one error after a change, never
  more: two identical errors where there was one is one new.
- The reference follows the project. Once a change is written, its own check
  is what the next change is compared with, so an error one change removed
  cannot be spent by another that brings it back: against the original's
  errors, the second would pass.
- The alignment is a line diff, so it holds whatever wrote the lines,
  formatter and import sorter included. However it pairs the lines, an error
  is new under it whenever its message appears more often than before in its
  file, so it can reject more than a comparison by message would, never less.

A name the checker cannot type -- an import it cannot resolve or finds no
types for, a decorator without types -- is ``Any`` to it wherever it goes, and
``Any`` accepts everything, so a change that misuses what that name carries is
invisible to the check. Such errors are named by :func:`makes_names_any`, and
the files they lie in by :func:`files_where_names_are_any`, for the run to
decline changing them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import difflib
import functools
import os
from pathlib import Path
import re
from types import MappingProxyType
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
    Tuple,
)

from .type_inference import TypeDiagnostic

__all__ = [
    "CheckedChange",
    "KnownErrors",
    "LineMap",
    "files_where_names_are_any",
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
class KnownErrors:
    """The errors a check of the project reported, and which files may have changed since.

    ``errors`` carry resolved paths in the project that check was made of.
    ``moved`` names, the same way, the files whose text a change accepted
    since then may have altered, so that a line in them no longer means what
    it meant to that check. ``texts`` holds, for each file with an error that
    a change may touch, the text the check saw, against which a changed file
    is aligned (:func:`unchanged_lines`).
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
    ) -> Tuple[TypeDiagnostic, ...]:
        """The errors of ``after`` these do not account for, as ``after`` reported them.

        ``after`` is a check of the project with a change made to ``changing``
        (resolved paths); ``where`` places each of its paths in the
        reference's project, since a run that refactors a private copy is
        checked there, and ``texts_after`` gives, by resolved path, the text
        that check saw of a file whose lines may have moved. In a file neither
        the change nor an earlier one has touched, an error must match one of
        these at the same line. In any other, one on a line the change left
        alone must match one on that line before it; the rest are compared by
        message with these errors on the lines the change replaced, new when
        there are more of a message than there were, and then every one of
        them is reported, since nothing says which is the new one. Each error
        of the reference accounts for one error after, never more.
        """
        shifted = self.moved | frozenset(changing)
        placed: Counter[Tuple[str, Optional[int], str]] = Counter(
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
        for path, indices in in_shifted.items():
            new.extend(self._new_in_changed_file(path, indices, after, texts_after(path)))
        return tuple(after[index] for index in sorted(new))

    def _new_in_changed_file(
        self,
        path: str,
        indices: Sequence[int],
        after: Sequence[TypeDiagnostic],
        text_after: Optional[str],
    ) -> List[int]:
        """Which of ``after[indices]``, all in ``path``, these errors of ``path`` do not account for."""
        text_before = self.texts.get(path)
        lines = (
            unchanged_lines(text_before, text_after)
            if text_before is not None and text_after is not None
            else LineMap(MappingProxyType({}), frozenset())
        )
        kept: Counter[Tuple[int, str]] = Counter()
        replaced: Counter[str] = Counter()
        for error in self.errors:
            if error.path != path:
                continue
            moved_to = lines.after_of.get(error.line) if error.line is not None else None
            if moved_to is None:
                replaced[error.message] += 1
            else:
                kept[(moved_to, error.message)] += 1
        new: List[int] = []
        written: Dict[str, List[int]] = {}
        for index in indices:
            error = after[index]
            if error.line is not None and error.line in lines.unchanged_after:
                place = (error.line, error.message)
                if kept[place]:
                    kept[place] -= 1
                else:
                    new.append(index)
            else:
                written.setdefault(error.message, []).append(index)
        for message, found in written.items():
            if len(found) > replaced[message]:
                new.extend(found)
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
    names_any: Mapping[str, Tuple[TypeDiagnostic, ...]], root: Path, changed: Collection[str]
) -> Optional[str]:
    """Where the original check leaves names it cannot type, and what the run does there.

    ``names_any`` is :func:`files_where_names_are_any` of the original's
    errors, and ``changed`` the files (resolved) the run may change: no change
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
        f"warning: {count} of these error(s) leave a name the checker cannot type (an "
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
