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

"""The files of the program that Towel reads whole, and the refusal when one does not parse here.

Some of Towel's checks read the whole program, not only the code being
refactored, for what could make a change unsafe: a decorator applied by hand
(``decorator_reach``), a builtin patched into a module (``namespace_writes``),
a module whose asserts pytest rewrites (``assert_rewriting``). Each reads the
files :func:`program_files` yields, so each leaves out the same directories.

A file one of them cannot parse was once skipped as a file that cannot run.
It can run, on a newer Python. A project that requires 3.12 may hold a PEP 701
f-string, which 3.11 rejects. Towel on 3.11 then missed the hand-applied
decorator beside it, and a function it moved code out of computed something
else. So a run refuses before anything is written when a file of the program
does not parse on the interpreter Towel runs on
(:func:`refuse_unparsed_program`). The refusal names each file and the
parser's complaint, and gives two remedies: run Towel on a Python that parses
it, or, if it is not meant to run, such as deliberately invalid test data,
``--exclude`` it, by its own name or its directory's. Each scan refuses in
the same words if it meets such a file itself (:func:`refuse_unparsed_file`).
Only the parse depends on the Python version: a file that does not decode in
its declared encoding runs on no Python, and is left alone, as one that
cannot be read is.

``--exclude`` means "do not refactor these", and a file it names is still
read by every scan, as evidence: a test suite excluded to keep it unchanged
still declines a change its ``mock.patch`` would notice. Only a file that is
both excluded and does not parse here is taken at the user's word as no part
of the program (:func:`excluded_by`).
"""

from __future__ import annotations

import ast
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence
from typing import Tuple

from .consumers import MAXIMUM_FILES, SKIPPED_DIRECTORIES
from .declared_python import PythonVersion, declared_newest_python, declared_requirement
from .declared_python import python_lower_bound
from .project_layout import find_project_root
from .source_files import is_environment, python_sources
from .source_text import decode_source
from .unification.bounded_cache import BoundedCache
from .unification.exceptions import UnparsedProgramError

_LEFT_OUT = SKIPPED_DIRECTORIES | {"site-packages", "dist-packages"}
"""Directories no whole-program scan reads: tool state, build outputs, installed packages."""


def program_directories(root: Path) -> Iterator[Tuple[str, List[str]]]:
    """Each directory below ``root`` that the whole-program scans read, with its file names, sorted.

    Left out are the directories every scan skips (``SKIPPED_DIRECTORIES``:
    version control, caches, build outputs, the usual environment names),
    installation directories, and any environment packages are installed
    into, whatever its name (``is_environment``). What ``--exclude`` names is
    read like the rest (:func:`excluded_by`). Symbolic links to directories
    are not followed, and an unreadable directory is passed over. Callers
    pick the files they read by suffix.
    """
    for parent, directories, files in os.walk(root, onerror=lambda _: None):
        directories[:] = sorted(
            name
            for name in directories
            if name not in _LEFT_OUT and not is_environment(Path(parent, name))
        )
        yield parent, sorted(files)


def program_files(root: Path) -> Iterator[Path]:
    """Every file :func:`program_directories` lists, as a path."""
    for parent, names in program_directories(root):
        for name in names:
            yield Path(parent, name)


def excluded_by(path: Path, root: Path, excluded_names: AbstractSet[str]) -> bool:
    """Whether ``--exclude`` names ``path`` below ``root``: its own name, or a directory's it lies in.

    Names match at any depth, as they do for what a run refactors
    (``python_sources``).
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return any(part in excluded_names for part in parts)


@dataclass(frozen=True)
class UnparsedFile:
    """A file of the program that does not parse on this interpreter, and what the parser said."""

    path: Path
    complaint: str
    """The parser's words: ``line 6: f-string: expecting '}'``, or why the file does not decode."""

    def describe(self, root: Path) -> str:
        return f"{_shown(self.path, root)}: {self.complaint}"


_Stamp = Tuple[str, int, int, int]
_COMPLAINTS: BoundedCache[_Stamp, Optional[str]] = BoundedCache(1 << 15)
"""What the parser said of each file version, None when it parsed: a run asks twice."""


def parse_failure(path: Path) -> Optional[UnparsedFile]:
    """Why ``path`` does not parse on this interpreter; None when it parses, or cannot run anywhere.

    Decoded as the interpreter decodes it (a byte-order mark or coding
    cookie, else UTF-8), then parsed. Only the parse depends on the Python
    version. A file that cannot be read, or does not decode in its declared
    encoding, runs on no Python, and is left alone as it always was.
    """
    try:
        status = path.stat()
    except OSError:
        return None
    stamp = (str(path), status.st_mtime_ns, status.st_size, status.st_ino)
    if stamp in _COMPLAINTS:
        complaint = _COMPLAINTS[stamp]
    else:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        complaint = _COMPLAINTS.put(stamp, _complaint(data, path))
    return None if complaint is None else UnparsedFile(path, complaint)


def _complaint(data: bytes, path: Path) -> Optional[str]:
    try:
        text = decode_source(data)
    except (UnicodeError, SyntaxError, LookupError):
        return None  # Not text in its declared encoding, or the cookie names no codec.
    try:
        with warnings.catch_warnings():
            # Test data is full of invalid escapes; reading it is not the place to say so.
            warnings.simplefilter("ignore")
            ast.parse(text, filename=str(path))
    except (SyntaxError, ValueError, RecursionError) as error:
        # ValueError: a null byte, before 3.12 made that a SyntaxError.
        return _described(error)
    return None


def _described(error: BaseException) -> str:
    """What a failed parse says, as the refusal shows it: ``line 6: f-string: expecting '}'``."""
    if isinstance(error, SyntaxError):
        return error.msg if not error.lineno else f"line {error.lineno}: {error.msg}"
    if isinstance(error, RecursionError):
        return "it nests too deeply for this Python's parser"
    return str(error)


def unparsed_program_files(
    target: Path, excluded_names: AbstractSet[str] = frozenset()
) -> Tuple[UnparsedFile, ...]:
    """The files of ``target``'s program that do not parse here, sorted by path.

    That is every Python file the whole-program scans read below the
    project root (:func:`program_files`), and every file the run would
    analyze in ``target`` itself, which may include directories the scans
    skip, such as a ``build`` inside it. Past the scans' own limit the
    program is not read whole, and they say so themselves; this reads as
    far as they do.
    """
    return _unparsed_below(target.resolve(), find_project_root(target).resolve(), excluded_names)


def _unparsed_below(
    target: Path, root: Path, excluded_names: AbstractSet[str]
) -> Tuple[UnparsedFile, ...]:
    """What does not parse of the program below ``root``, and of the files the run analyzes.

    A file the run analyzes always counts: an explicit file target is
    analyzed whatever ``--exclude`` names. Any other that ``--exclude``
    names is taken to be no part of the program.
    """
    candidates: Dict[Path, None] = {}
    count = 0
    for path in program_files(root):
        if path.suffix != ".py":
            continue
        count += 1
        if count > MAXIMUM_FILES:
            break
        if not excluded_by(path, root, excluded_names):
            candidates[path] = None
    own = [target] if target.is_file() else python_sources(target, excluded=excluded_names)
    candidates.update(dict.fromkeys(own))
    found = (parse_failure(path) for path in sorted(candidates))
    return tuple(failure for failure in found if failure is not None)


def refuse_unparsed_program(target: Path, excluded_names: Iterable[str] = ()) -> None:
    """Refuse a run over ``target`` before anything is written, when its program cannot be read whole.

    It refuses when a file of the program does not parse here
    (:func:`unparsed_program_files`), and when ``--exclude`` names a
    directory ``target`` is or lies in: the run would change what it was
    told to leave alone, and the program's import model, which reads
    nothing in an excluded directory, would not know the target's names.
    """
    excluded = frozenset(excluded_names)
    root = find_project_root(target).resolve()
    resolved = target.resolve()
    holders = sorted(excluded & _holding_names(resolved, root))
    if holders:
        raise ValueError(
            f"--exclude {holders[0]} would leave out {target}, the code being refactored."
            " Exclude only directories or files inside it."
        )
    unparsed = _unparsed_below(resolved, root, excluded)
    if unparsed:
        raise unparsed_refusal(unparsed, root, target=resolved)


def refuse_unparsed_file(
    path: Path,
    root: Path,
    excluded_names: AbstractSet[str] = frozenset(),
    error: Optional[BaseException] = None,
) -> None:
    """Raise the refusal when ``path``, a file below ``root``, does not parse here and is not excluded.

    For a whole-program scan that meets a file it cannot parse: the run
    refuses before it starts when the program holds one, so meeting one
    means the program changed, or the scan was asked outside a run. A file
    ``--exclude`` names is then no part of the program, and the scan passes
    it over, as it does one that does not decode. ``error`` is what the
    scan's own parse raised: where this module's parse succeeds but the
    file decodes, as under ``-W error`` for an invalid escape, the scan
    still could not read it, and that refuses too.
    """
    resolved = root.resolve()
    if excluded_by(path, resolved, excluded_names):
        return
    failure = parse_failure(path)
    if failure is None and error is not None and _decodes(path):
        failure = UnparsedFile(path, _described(error))
    if failure is not None:
        raise unparsed_refusal((failure,), resolved)


def _decodes(path: Path) -> bool:
    try:
        decode_source(path.read_bytes())
    except (OSError, UnicodeError, SyntaxError, LookupError):
        return False
    return True


def unparsed_refusal(
    unparsed: Sequence[UnparsedFile], root: Path, *, target: Optional[Path] = None
) -> UnparsedProgramError:
    """The refusal of a run whose program holds ``unparsed``, with its two remedies."""
    running: PythonVersion = (sys.version_info[0], sys.version_info[1])
    subject = "this file" if len(unparsed) == 1 else f"these {len(unparsed)} files"
    verb = "does" if len(unparsed) == 1 else "do"
    requirement = declared_requirement(root)
    lines = [
        f"Refusing to refactor: {subject} of the program {verb} not parse on"
        f" Python {_version(running)}, which Towel is running on:",
        *(f"  {failure.describe(root)}" for failure in unparsed),
        "Towel reads the whole program for what could make a change unsafe, such as a"
        " decorator applied by hand or a builtin a test patches, and a file it cannot parse"
        " may still run on a newer Python, doing what Towel never saw.",
        _python_remedy(
            declared_newest_python(root),
            python_lower_bound(requirement) if requirement is not None else None,
            running,
        ),
        *_exclusion_remedy(unparsed, root, target),
    ]
    return UnparsedProgramError("\n".join(lines))


def _python_remedy(
    newest: Optional[PythonVersion], oldest: Optional[PythonVersion], running: PythonVersion
) -> str:
    """Which Python to run Towel on: the newest the project declares, else the newest there is.

    Where the project requires a newer Python than this one, as sphinx
    requiring 3.12 does of Towel on 3.11, that is said too: its own code may
    be what does not parse.
    """
    required = (
        f"; it requires Python {_version(oldest)} or newer"
        if oldest is not None and oldest > running
        else ""
    )
    if newest is None:
        return (
            "Run Towel on a Python that parses it: the newest Python, since the project"
            f" declares no newest version it supports{required}."
        )
    if newest > running:
        return (
            f"Run Towel on a Python that parses it: Python {_version(newest)}, the newest the"
            f" project declares it supports{required}."
        )
    return (
        "Run Towel on a Python that parses it, if there is one: the newest the project"
        f" declares it supports is Python {_version(newest)}, which is no newer than this one."
    )


def _exclusion_remedy(
    unparsed: Sequence[UnparsedFile], root: Path, target: Optional[Path]
) -> List[str]:
    """The ``--exclude`` that takes each file out of the program, and what it cannot reach.

    Each file gets a name that clears it (:func:`exclusion_names`); only a
    file the run was explicitly given to refactor has none, since an
    exclusion never leaves that out.
    """
    names = exclusion_names(unparsed, root, target)
    reached = {failure.path for failure in unparsed if failure.path in names}
    lines: List[str] = []
    if reached:
        lines.append(
            "Or, if it is not meant to run, such as deliberately invalid test data, leave it"
            " out: "
            + " ".join(f"--exclude {name}" for name in sorted(set(names.values())))
            + ". Towel then refactors no directory or file of that name, and takes one that does"
            " not parse for no part of the program."
        )
    given = [failure for failure in unparsed if failure.path not in reached]
    if given:
        lines.append(
            ", ".join(_shown(failure.path, root) for failure in given)
            + ": the file the run was given to refactor, which no --exclude leaves out; fix it,"
            " or run Towel on a Python that parses it."
        )
    return lines


def exclusion_names(
    unparsed: Sequence[UnparsedFile], root: Path, target: Optional[Path]
) -> Dict[Path, str]:
    """For each file of ``unparsed``, a name that ``--exclude`` accepts and that clears it.

    A directory shared by two or more of the files is named once for them
    all (black's ``tests/data/cases``); otherwise the file is named itself,
    matching only files of that name (unidecode's ``benchmark.py`` at the
    project root). A directory the target is or lies in is never named,
    since that exclusion is refused, nor is a name ``--exclude`` would not
    take; failing the file's own name, the nearest directory above it that
    qualifies is. A file the run was given explicitly is analyzed whatever
    the exclusions, and gets none.
    """
    held = _holding_names(target, root) if target is not None else frozenset()
    given = target if target is not None and target.is_file() else None

    def usable(name: str) -> bool:
        return name not in held and is_exclusion_name(name)

    sharing: Dict[Path, int] = {}
    for failure in unparsed:
        sharing[failure.path.parent] = sharing.get(failure.path.parent, 0) + 1
    names: Dict[Path, str] = {}
    for failure in unparsed:
        path = failure.path
        if path == given or not path.is_relative_to(root):
            continue
        parent = path.parent
        directories = [
            directory
            for directory in (parent, *parent.parents)
            if directory != root and directory.is_relative_to(root)
        ]
        preferred = [parent] if parent in directories and sharing[parent] > 1 else []
        if path.name == "__init__.py" and parent in directories:
            preferred = [parent]
        choices = [
            *(directory.name for directory in preferred),
            path.name,
            *(directory.name for directory in directories),
        ]
        chosen = next((name for name in choices if usable(name)), None)
        if chosen is not None:
            names[path] = chosen
    return names


def is_exclusion_name(value: str) -> bool:
    """Whether ``--exclude`` takes ``value``: a directory's or a file's name, not a path or a pattern."""
    return bool(value) and not any(mark in value for mark in ("/", os.sep, "*", "?", "["))


def _holding_names(target: Path, root: Path) -> FrozenSet[str]:
    """The names of the directories below ``root`` that hold ``target``, ``target`` included."""
    directory = target if target.is_dir() else target.parent
    if directory == root or not directory.is_relative_to(root):
        return frozenset()
    return frozenset(directory.relative_to(root).parts)


def _version(version: PythonVersion) -> str:
    return f"{version[0]}.{version[1]}"


def _shown(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
