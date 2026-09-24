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
by line would reject every change above any pre-existing error. Where lines
cannot have moved they are the most exact evidence there is, and where they
can they are none. So a file the change and the run have left alone is
compared by file, line and message, and a file whose text may have changed by
file and message alone, as a multiset: there an error is new when its message
appears more often than before. The message includes the checker's error
code, so two errors differ when their codes do.

What this can mistake, and which way it errs:

- A message that embeds a line (mypy's ``Name "x" already defined on line
  12``) changes when that line moves, so it reappears as new and the change is
  rejected. It fails closed: a shift can cost a change, never hide an error.
- In a changed file, an error that disappears where another with the very
  same message appears is taken to have moved, as a duplicated block's error
  does when the block moves into the helper: it is the same error, and it was
  already there. Only its line could tell the two apart, and the change has
  made the line meaningless.
- An error of the reference accounts for one error after a change, never
  more: two identical errors where there was one is one new.
- The reference follows the project. Once a change is written, its own check
  is what the next change is compared with, so an error one change removed
  cannot be spent by another that brings it back: against the original's
  errors, the second would pass.

A name the checker cannot type -- an import it cannot resolve or finds no
types for, a decorator without types -- is ``Any`` to it wherever it goes, and
``Any`` accepts everything, so a change that misuses what that name carries is
invisible to the check. Such errors are named by :func:`makes_names_any`, and
the files they lie in by :func:`files_where_names_are_any`, for the run to
decline changing them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
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
    "files_where_names_are_any",
    "makes_names_any",
    "names_any_warning",
    "pre_existing_summary",
    "resolved_path",
]


def resolved_path(path: str) -> str:
    """``path`` as a comparison needs it: absolute, with every symbolic link resolved."""
    return os.path.realpath(path)


@dataclass(frozen=True)
class KnownErrors:
    """The errors a check of the project reported, and which files may have changed since.

    ``errors`` carry resolved paths in the project that check was made of.
    ``moved`` names, the same way, the files whose text a change accepted
    since then may have altered, so that a line in them no longer means what
    it meant to that check.
    """

    errors: Tuple[TypeDiagnostic, ...] = ()
    moved: FrozenSet[str] = frozenset()

    @classmethod
    def of(
        cls, errors: Iterable[TypeDiagnostic], where: Callable[[str], str] = resolved_path
    ) -> "KnownErrors":
        """What a check reported, its paths as ``where`` places them in the reference's project."""
        return cls(tuple(TypeDiagnostic(where(e.path), e.message, e.line) for e in errors))

    def moving(self, paths: Iterable[str]) -> "KnownErrors":
        """These errors, with ``paths`` (resolved) among the files whose lines may have moved."""
        return replace(self, moved=self.moved | frozenset(paths))

    def introduced(
        self,
        after: Sequence[TypeDiagnostic],
        changing: Collection[str] = (),
        *,
        where: Callable[[str], str] = resolved_path,
    ) -> Tuple[TypeDiagnostic, ...]:
        """The errors of ``after`` these do not account for, as ``after`` reported them.

        ``after`` is a check of the project with a change made to ``changing``
        (resolved paths); ``where`` places each of its paths in the
        reference's project, since a run that refactors a private copy is
        checked there. In a file neither the change nor an earlier one has
        touched, an error must match one of these at the same line; in any
        other file, the errors of one message are new when there are more of
        them than there were, and then every one of them is reported, since
        nothing says which is the new one. Each error of the reference
        accounts for one error after, never more.
        """
        shifted = self.moved | frozenset(changing)
        placed: Counter[Tuple[str, Optional[int], str]] = Counter(
            (error.path, error.line, error.message)
            for error in self.errors
            if error.path not in shifted
        )
        counted: Counter[Tuple[str, str]] = Counter(
            (error.path, error.message) for error in self.errors if error.path in shifted
        )
        new: List[int] = []
        loose: Dict[Tuple[str, str], List[int]] = {}
        for index, error in enumerate(after):
            path = where(error.path)
            if path in shifted:
                loose.setdefault((path, error.message), []).append(index)
                continue
            place = (path, error.line, error.message)
            if placed[place]:
                placed[place] -= 1
            else:
                new.append(index)
        for message, indices in loose.items():
            if len(indices) > counted[message]:
                new.extend(indices)
        return tuple(after[index] for index in sorted(new))


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
            f"The rest lie in {len(elsewhere)} file(s) the run does not change ({shown}{more}), "
            "where a use of such a name is checked against Any."
        )
    return "\n".join(lines)
