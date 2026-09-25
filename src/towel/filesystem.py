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

"""Disposable copies: the private stage a run refactors, and what it publishes from it.

Every run refactors a private copy of the whole project (``staged_project``)
and touches nothing the user owns until it has succeeded, its cold
confirmation included. An out-of-place run then publishes the target's
counterpart to the output (``copy_project``), all at once; an in-place run
applies the combined change to the project as one journaled plan
(``staged_changes``). A run that fails, or is interrupted, leaves the project
and the output path as they were.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePath
import shutil
import stat
import tempfile
from typing import Iterable, Iterator, List, Mapping, Tuple

from .changes import JOURNAL_PREFIX, SET_ASIDE_PREFIX, ChangePlan, FileChange, StaleSource
from .source_files import TOOL_DIRECTORIES, is_environment, is_probe_file


def _case_collisions(relatives: Iterable[PurePath]) -> List[Tuple[PurePath, PurePath]]:
    """Pairs of relative paths that name the same file on a case-insensitive volume."""
    seen: dict[str, PurePath] = {}
    collisions = []
    for relative in relatives:
        key = str(relative).casefold()
        if key in seen and seen[key] != relative:
            collisions.append((seen[key], relative))
        seen.setdefault(key, relative)
    return collisions


def _refuse_collisions_on(
    collisions: List[Tuple[PurePath, PurePath]], destination_parent: Path, volume: str
) -> None:
    """Raise when ``destination_parent``'s volume would merge the first of ``collisions``.

    ``copytree`` would write the second over the first without a word.
    """
    if not collisions:
        return
    with tempfile.NamedTemporaryFile(prefix=".towel-case-", dir=destination_parent) as probe:
        swapped = Path(probe.name).with_name(Path(probe.name).name.swapcase())
        insensitive = swapped.exists()
    if insensitive:
        first, second = collisions[0]
        raise ValueError(
            f"{first} and {second} differ only by case and the {volume} volume does not"
            " distinguish them"
        )


def _refuse_case_collisions(source: Path, destination_parent: Path) -> None:
    """Refuse to copy two paths that differ only by case onto a case-insensitive volume."""
    relatives = (path.relative_to(source) for path in source.rglob("*"))
    _refuse_collisions_on(_case_collisions(relatives), destination_parent, "output")


def _refuse_unusable_output(
    source: Path, destination: Path, *, allow_empty: bool
) -> Tuple[Path, Path]:
    """``(source, destination)`` made absolute, or ValueError when the copy could not go there."""
    source = source.resolve()
    destination = destination.absolute()
    if destination.is_symlink():
        raise ValueError("Output must not be a symlink")
    resolved = destination.resolve()
    if source == resolved or source in resolved.parents or resolved in source.parents:
        raise ValueError("Copy input and output must be distinct and non-overlapping")
    if destination.exists() and not (
        allow_empty and destination.is_dir() and not any(destination.iterdir())
    ):
        raise ValueError("Output already exists")
    return source, destination


def refuse_unusable_output(source: Path, destination: Path, *, allow_empty: bool = False) -> None:
    """Refuse now what ``copy_project`` would refuse, before a long run is spent on it."""
    _refuse_unusable_output(source, destination, allow_empty=allow_empty)


def copy_project(source: Path, destination: Path, *, allow_empty: bool = False) -> None:
    """Copy into a private sibling, so copy failure never leaves a partial output.

    Callers must have exclusive write access to the destination and its parent.
    Existing empty directories are allowed only for the directory API.
    """
    source, destination = _refuse_unusable_output(source, destination, allow_empty=allow_empty)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        _refuse_case_collisions(source, destination.parent)
    with tempfile.TemporaryDirectory(prefix=".towel-copy-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "payload"
        if source.is_dir():
            shutil.copytree(source, staged, symlinks=True)
        else:
            shutil.copy2(source, staged)
        # Check again after the potentially long copy. Exclusive access is still
        # required for the final check/rename interval.
        if destination.exists() and not (
            allow_empty and destination.is_dir() and not any(destination.iterdir())
        ):
            raise ValueError("Output appeared or changed during copying")
        os.replace(staged, destination)


STAGE_SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        *TOOL_DIRECTORIES,
    }
)
"""Directories outside the target that hold no input to the analysis; as the checker copy skips.

An environment, known by what it holds (``source_files.is_environment``), is skipped too.
"""

_TRANSIENT_PREFIXES = ("towel-stage-", ".towel-copy-", JOURNAL_PREFIX, SET_ASIDE_PREFIX)
"""Another run's stage or half-published output, or a journal, should the project contain one.

None is an input to the analysis, and a journal copied into the stage
names the stage's copies of its files: the run's own write there then
refused as though the project had an interrupted change pending, and named
the stage in its remedy. A journal belongs to the files it sits beside, so
none is copied into an output either.
"""

STAGED_SUFFIXES = frozenset({".py", ".pyi", ".toml", ".json", ".ini", ".cfg"})
STAGED_NAMES = frozenset({"py.typed", ".gitignore"})
"""What of the project outside the target the stage holds: sources, stubs, configuration."""


class ProjectTooLarge(ValueError):
    """The project around a target is too large to stage.

    Staging it would crawl, and staging part of it would bring back exactly
    the blindness the stage exists to remove.
    """


@dataclass(frozen=True)
class StagedProject:
    """A private copy of a whole project, and where the refactored target will be published.

    ``target`` is the counterpart of ``origin_target`` inside ``root``, the
    copy of ``origin_root``. Every path the run reports is about the stage,
    which is deleted when the run ends; ``public`` says which path the user
    knows it by: the output for the target, the original for the rest. An
    in-place stage's output is its origin target, and ``staged`` records the
    digest of every file staged from the target, which is how
    ``staged_changes`` tells what the run rewrote from what someone else did.
    """

    origin_root: Path
    origin_target: Path
    root: Path
    target: Path
    output: Path
    staged: Mapping[Path, str] = field(default_factory=dict)

    @property
    def in_place(self) -> bool:
        """Whether the result is written back over the project rather than to a new output."""
        return self.output == self.origin_target

    def public(self, path: str) -> str:
        """The path the user knows ``path`` by; itself when it is not in the stage."""
        candidate = Path(path)
        if candidate.is_absolute():
            if candidate == self.target or candidate.is_relative_to(self.target):
                return str(self.output / candidate.relative_to(self.target))
            if candidate.is_relative_to(self.root):
                return str(self.origin_root / candidate.relative_to(self.root))
        return path

    def public_text(self, text: str) -> str:
        """``text`` with every stage path in it replaced by the path the user knows it by."""
        # The target lies inside the root, so it is replaced first.
        text = text.replace(str(self.target), str(self.output))
        return text.replace(str(self.root), str(self.origin_root))


def _stage_plan(
    root: Path, target: Path, skipped: Iterable[Path], limit: int, *, whole_target: bool
) -> Tuple[List[PurePath], int]:
    """The project's inputs (relative to ``root``), and its Python file count.

    With ``whole_target`` the target is left out, to be copied whole as an
    output would be; otherwise it is staged by the rules the rest follows.
    Symlinked directories are listed, not entered, as ``copytree(symlinks=True)``
    treats them. Raises ``ProjectTooLarge`` past ``limit`` Python files.
    """
    avoided = {path.resolve() for path in skipped}
    planned: List[PurePath] = []
    python_files = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        directory = Path(parent)
        # A target copied whole is walked here only to be counted, under the
        # same rules as the rest.
        inside_target = whole_target and (directory == target or directory.is_relative_to(target))
        kept = []
        for name in sorted(directories):
            path = directory / name
            if (
                path in avoided
                or name in STAGE_SKIPPED_DIRECTORIES
                or name.startswith(_TRANSIENT_PREFIXES)
            ):
                continue
            if path.is_symlink():
                if not inside_target:
                    planned.append(path.relative_to(root))
                continue
            if is_environment(path):
                continue
            kept.append(name)
        directories[:] = kept
        for name in sorted(files):
            path = directory / name
            if name.endswith((".py", ".pyi")):
                python_files += 1
                if python_files > limit:
                    raise ProjectTooLarge(
                        f"The project around {target} ({root}) holds more than {limit} Python"
                        " files. A run refactors a private copy of the whole project, so that"
                        " nothing is written until it has succeeded and every decision sees the"
                        " modules around the target, and this one is too large to copy. Towel"
                        " takes the project to be the nearest directory with a pyproject.toml,"
                        " setup.cfg or setup.py; give the code one, or move it out of the larger"
                        " tree."
                    )
            if inside_target or (whole_target and path == target):
                continue
            if path in avoided or is_probe_file(path):
                continue
            if path.suffix in STAGED_SUFFIXES or name in STAGED_NAMES:
                planned.append(path.relative_to(root))
    return planned, python_files


def _copy_entry(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        os.symlink(os.readlink(source), destination)
    else:
        shutil.copy2(source, destination)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@contextmanager
def staged_project(
    root: Path, target: Path, output: Path, *, limit: int
) -> Iterator[StagedProject]:
    """A private copy of the project at ``root`` in which ``target`` can be refactored.

    The project contributes its sources, stubs and configuration, at the same
    relative places, so that layout discovery, the import graph and the
    import-time-effect scan see the project as it stands. For an output
    elsewhere the target is copied whole, as ``copy_project`` will copy it
    there. For an output that is the target itself -- an in-place run -- the
    target is staged like the rest, since only the files the run rewrites
    will ever go back, and each staged file's digest is kept so that
    ``staged_changes`` can tell them apart. The copy lives outside the
    project, where no scan of it can find the stage, and is removed however
    the block ends.
    """
    root, target = root.resolve(), target.resolve()
    if not (target == root or target.is_relative_to(root)):
        raise ValueError(f"{target} is not inside its project root {root}")
    in_place = output.absolute() == target or output.resolve() == target
    planned, _ = _stage_plan(
        root, target, () if in_place else (output,), limit, whole_target=not in_place
    )
    if in_place and target.is_file() and target.relative_to(root) not in planned:
        # A file refactored on request whatever its suffix still has to be there.
        planned.append(target.relative_to(root))
    with tempfile.TemporaryDirectory(prefix="towel-stage-") as temporary:
        staged_root = Path(temporary).resolve() / (root.name or "project")
        staged_target = staged_root / target.relative_to(root)
        relatives: List[PurePath] = list(planned)
        if in_place:
            pass  # the plan already holds the target's inputs
        elif target.is_dir():
            relatives.extend(path.relative_to(root) for path in target.rglob("*"))
        else:
            relatives.append(target.relative_to(root))
        _refuse_collisions_on(_case_collisions(relatives), Path(temporary), "staging")
        staged_root.mkdir()
        for relative in planned:
            _copy_entry(root / relative, staged_root / relative)
        staged_target.parent.mkdir(parents=True, exist_ok=True)
        if in_place:
            if target.is_dir():
                staged_target.mkdir(parents=True, exist_ok=True)
            yield StagedProject(
                root,
                target,
                staged_root,
                staged_target,
                target,
                _staged_digests(root, target, staged_root, planned),
            )
            return
        if target.is_dir():
            if staged_target.exists():
                # The target is the root itself; the loop above copied nothing.
                staged_target.rmdir()
            shutil.copytree(target, staged_target, symlinks=True, ignore=_transient_entries)
        else:
            shutil.copy2(target, staged_target)
        yield StagedProject(root, target, staged_root, staged_target, output)


def _transient_entries(directory: str, names: List[str]) -> List[str]:
    """The ``copytree`` ignore that leaves Towel's own transient directories out of a target."""
    return [
        name
        for name in names
        if name.startswith(_TRANSIENT_PREFIXES) and os.path.isdir(os.path.join(directory, name))
    ]


def _staged_digests(
    root: Path, target: Path, staged_root: Path, planned: Iterable[PurePath]
) -> Mapping[Path, str]:
    """The digest of each regular file staged from the target, by the project path it copies.

    Taken from the copies, not the originals: the copy is what the run starts
    from, so an original edited even while it was being copied no longer
    matches, and its result is refused rather than written over the edit.
    """
    digests = {}
    for relative in planned:
        original, copy = root / relative, staged_root / relative
        if not (original == target or original.is_relative_to(target)):
            continue
        if copy.is_file() and not copy.is_symlink():
            digests[original] = _digest(copy.read_bytes())
    return digests


def staged_changes(stage: StagedProject) -> ChangePlan:
    """What an in-place run changes in the project: every staged target file it rewrote.

    Each change goes from the bytes the file held when it was staged to what
    the stage now holds, and is applied with the rest as one journaled plan.
    A file that no longer holds what was staged was edited while the run
    refactored a copy of it; writing the result would discard that edit, so
    nothing is written at all.
    """
    changes: List[FileChange] = []
    for original, digest in sorted(stage.staged.items()):
        copy = stage.target / original.relative_to(stage.origin_target)
        after = copy.read_bytes()
        if _digest(after) == digest:
            continue
        before = original.read_bytes()
        if _digest(before) != digest:
            raise StaleSource(
                f"{original} changed while Towel was refactoring a copy of the project, so"
                " the result was not written: it would have discarded that change. Nothing"
                " was written; run again on the project as it now stands."
            )
        changes.append(FileChange(original, before, after, stat.S_IMODE(original.stat().st_mode)))
    return ChangePlan(tuple(changes))
