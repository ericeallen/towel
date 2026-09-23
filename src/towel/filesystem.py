"""Disposable copies: the private stage a run refactors, and the output it publishes.

An out-of-place run must decide exactly what an in-place run on the same
project would, so it refactors a private copy of the whole project
(``staged_project``) and publishes only the target's counterpart from it
(``copy_project``), all at once, when the run succeeds.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path, PurePath
import shutil
import tempfile
from typing import Iterable, Iterator, List, Tuple

from .source_files import is_probe_file


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
        "__pycache__",
        "venv",
        "env",
        "node_modules",
    }
)
"""Directories outside the target that hold no input to the analysis; as the checker copy skips.

A directory holding ``pyvenv.cfg`` is an environment and is skipped too.
"""

_TRANSIENT_PREFIXES = ("towel-stage-", ".towel-copy-")
"""Another run's stage or half-published output, should the project contain either."""

STAGED_SUFFIXES = frozenset({".py", ".pyi", ".toml", ".json", ".ini", ".cfg"})
STAGED_NAMES = frozenset({"py.typed", ".gitignore"})
"""What of the project outside the target the stage holds: sources, stubs, configuration."""


class ProjectTooLarge(ValueError):
    """The project around an out-of-place target is too large to stage.

    Staging it would crawl, and staging part of it would bring back exactly
    the blindness the stage exists to remove.
    """


@dataclass(frozen=True)
class StagedProject:
    """A private copy of a whole project, and where the refactored target will be published.

    ``target`` is the counterpart of ``origin_target`` inside ``root``, the
    copy of ``origin_root``. Every path the run reports is about the stage,
    which is deleted when the run ends; ``public`` says which path the user
    knows it by: the output for the target, the original for the rest.
    """

    origin_root: Path
    origin_target: Path
    root: Path
    target: Path
    output: Path

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


def _is_environment(directory: Path) -> bool:
    return (directory / "pyvenv.cfg").is_file()


def _stage_plan(
    root: Path, target: Path, skipped: Iterable[Path], limit: int
) -> Tuple[List[PurePath], int]:
    """The project's inputs outside ``target`` (relative to ``root``), and its Python file count.

    Symlinked directories are listed, not entered, as ``copytree(symlinks=True)``
    treats them. Raises ``ProjectTooLarge`` past ``limit`` Python files.
    """
    avoided = {path.resolve() for path in skipped}
    planned: List[PurePath] = []
    python_files = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        directory = Path(parent)
        # The target is copied whole, as the output would be; it is walked
        # only to be counted, under the same rules as the rest.
        inside_target = directory == target or directory.is_relative_to(target)
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
            if _is_environment(path):
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
                        " files. An out-of-place run copies the whole project so that it decides"
                        " exactly what an in-place run would, and this one is too large to copy."
                        " Copy the project yourself and refactor the copy in place."
                    )
            if inside_target or path == target or path in avoided or is_probe_file(path):
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


@contextmanager
def staged_project(
    root: Path, target: Path, output: Path, *, limit: int
) -> Iterator[StagedProject]:
    """A private copy of the project at ``root`` in which ``target`` can be refactored.

    The target is copied whole, as ``copy_project`` would copy it to the
    output; the rest of the project contributes its sources, stubs and
    configuration, at the same relative places, so that layout discovery,
    the import graph and the import-time-effect scan see the project an
    in-place run would see. The copy lives outside the project, where no
    scan of it can find the stage, and is removed however the block ends.
    """
    root, target = root.resolve(), target.resolve()
    if not (target == root or target.is_relative_to(root)):
        raise ValueError(f"{target} is not inside its project root {root}")
    planned, _ = _stage_plan(root, target, (output,), limit)
    with tempfile.TemporaryDirectory(prefix="towel-stage-") as temporary:
        staged_root = Path(temporary).resolve() / (root.name or "project")
        staged_target = staged_root / target.relative_to(root)
        relatives: List[PurePath] = list(planned)
        if target.is_dir():
            relatives.extend(path.relative_to(root) for path in target.rglob("*"))
        else:
            relatives.append(target.relative_to(root))
        _refuse_collisions_on(_case_collisions(relatives), Path(temporary), "staging")
        staged_root.mkdir()
        for relative in planned:
            _copy_entry(root / relative, staged_root / relative)
        staged_target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            if staged_target.exists():
                # The target is the root itself; the loop above copied nothing.
                staged_target.rmdir()
            shutil.copytree(target, staged_target, symlinks=True)
        else:
            shutil.copy2(target, staged_target)
        yield StagedProject(root, target, staged_root, staged_target, output)
