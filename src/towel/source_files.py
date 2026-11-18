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

"""Discover project Python sources with one exclusion policy for every command."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass, field
from functools import wraps
import os
import subprocess
from pathlib import Path
from typing import Callable, Concatenate, Iterable, Iterator, Optional, ParamSpec, Protocol, TypeVar

from .diagnostics import LOG

PROBE_PREFIX = "_towel_probe_"

TOOL_DIRECTORIES = frozenset({"__pycache__", "node_modules"})
ANALYSIS_DIRECTORIES = TOOL_DIRECTORIES | {"build", "dist", "site-packages"}
"""Build products, installed dependencies and caches, outside extraction."""


@dataclass
class SourcePolicy:
    """Run-owned input policy and diagnostics; nested phases share the same warnings."""

    strict: bool
    include_ignored: bool
    skipped: dict[Path, str] = field(default_factory=dict)
    omitted: dict[Path, str] = field(default_factory=dict)
    excluded_names: frozenset[str] = frozenset()
    copies: tuple[tuple[Path, Path], ...] = ()

    def original(self, path: Path) -> Path:
        """One input identity, whether encountered before staging or in a private copy."""
        path = path.resolve()
        for original, copied in reversed(self.copies):
            if path.is_relative_to(copied):
                path = original / path.relative_to(copied)
        return path


_SOURCE_POLICY: ContextVar[Optional[SourcePolicy]] = ContextVar("towel_source_policy", default=None)


def current_source_policy() -> Optional[SourcePolicy]:
    return _SOURCE_POLICY.get()


def source_policy_context(*, strict: bool, include_ignored: bool = False) -> Context:
    """Run nested phases with the same policy, without ever binding it in the caller.

    Context.run restores the caller in the interpreter, including when an alarm
    interrupts setup or teardown. A Python finally around ContextVar.reset has
    interruption windows on both sides of the body.
    """
    context = copy_context()
    outer = current_source_policy()
    if outer is None or (outer.strict, outer.include_ignored) != (strict, include_ignored):
        context.run(_SOURCE_POLICY.set, SourcePolicy(strict, include_ignored))
    return context


class _SourceOptions(Protocol):
    @property
    def strict(self) -> bool:
        """Whether an unreadable source refuses the run."""
        ...

    @property
    def include_ignored(self) -> bool:
        """Whether Git-ignored sources participate in analysis."""
        ...


_Options = TypeVar("_Options", bound=_SourceOptions)
_P = ParamSpec("_P")
_R = TypeVar("_R")


def with_source_policy(
    action: Callable[Concatenate[_Options, _P], _R],
) -> Callable[Concatenate[_Options, _P], _R]:
    """Scope source policy and any analysis caches installed by this call."""

    @wraps(action)
    def run(options: _Options, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        context = source_policy_context(
            strict=options.strict, include_ignored=options.include_ignored
        )
        return context.run(action, options, *args, **kwargs)

    return run


def skipped_source_paths() -> tuple[Path, ...]:
    """Unavailable original inputs, absent from every checker view in a relaxed run."""
    policy = current_source_policy()
    return tuple(policy.skipped) if policy is not None and not policy.strict else ()


def source_is_skipped(path: Path) -> bool:
    """Whether an input or its staged copy is beneath an unavailable input."""
    policy = current_source_policy()
    if policy is None or policy.strict:
        return False
    original = policy.original(path)
    return any(original.is_relative_to(skipped) for skipped in policy.skipped)


def warn_skipped(path: Path, reason: str) -> None:
    """Name an unavailable input once during a relaxed run."""
    policy = _SOURCE_POLICY.get()
    path = policy.original(path) if policy is not None else path
    if policy is None or path not in policy.skipped:
        LOG.warning("Skipping %s: %s", path, reason)
    if policy is not None:
        policy.skipped[path] = reason


@contextmanager
def source_copy(original: Path, copied: Path) -> Iterator[None]:
    """Share input identities across the lifetime of a run's private stage."""
    policy = _SOURCE_POLICY.get()
    if policy is None:
        yield
        return
    previous = policy.copies
    policy.copies += ((original.resolve(), copied.resolve()),)
    try:
        yield
    finally:
        policy.copies = previous


def skip_unreadable(error: OSError) -> None:
    """Walk error policy: strict refuses with the directory's name, relaxed warns."""
    policy = _SOURCE_POLICY.get()
    path = Path(str(error.filename)) if error.filename is not None else Path(".")
    if policy is not None and policy.excluded_names.intersection(path.parts):
        return
    if policy is None or policy.strict:
        if policy is None:
            raise error
        raise PermissionError(
            f"--exclude {path.name} leaves out this unreadable input: {path}: "
            f"{error.strerror or error}"
        ) from error
    warn_skipped(path, f"it cannot be read: {error.strerror or error}")


def readable_directory(directory: Path) -> bool:
    """Require both listing and search access before inspecting a directory's children.

    scandir alone succeeds on mode 0400 directories. Resolving the literal
    dot component asks the filesystem to check search permission too, without
    changing the working directory or guessing from mode bits instead of ACLs.
    """
    try:
        os.stat(os.path.join(directory, "."))
        with os.scandir(directory):
            pass
    except OSError as error:
        skip_unreadable(OSError(error.errno, error.strerror, str(directory)))
        return False
    return True


def ignored_by_git(directory: Path, paths: Iterable[Path]) -> frozenset[Path]:
    """Ask Git itself, preserving negations, nested rules and tracked-file exceptions."""
    candidates = tuple(paths)
    if not candidates:
        return frozenset()
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "check-ignore", "--stdin", "-z"],
            input=b"".join(os.fsencode(path.absolute()) + b"\0" for path in candidates),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except FileNotFoundError:
        return frozenset()
    except (subprocess.TimeoutExpired, OSError) as error:
        LOG.warning("Cannot read Git ignore rules: %s; including files Git may ignore", error)
        return frozenset()
    if result.returncode not in (0, 1):
        if b"not a git repository" in result.stderr:
            return frozenset()
        LOG.warning(
            "Cannot read Git ignore rules: %s; including files Git may ignore",
            os.fsdecode(result.stderr).strip(),
        )
        return frozenset()
    ignored = {os.fsdecode(part) for part in result.stdout.split(b"\0") if part}
    return frozenset(path for path in candidates if str(path.absolute()) in ignored)


def is_environment(directory: Path) -> bool:
    """Whether ``directory`` is an environment packages are installed into, by its marker.

    Every venv and virtualenv has a ``pyvenv.cfg`` and every conda
    environment a ``conda-meta`` directory; a project's own package called
    ``env`` or ``venv`` has neither.
    """
    try:
        return (directory / "pyvenv.cfg").is_file() or (directory / "conda-meta").is_dir()
    except OSError:
        # Let the walker name the unreadable directory, not a marker inside it.
        return False


def is_probe_file(path: Path) -> bool:
    """Whether the filename belongs to an ephemeral type-checker probe."""
    return path.name.startswith(PROBE_PREFIX)


def _is_python_source(path: Path) -> bool:
    """A regular Python file whose name does not belong to a checker probe."""
    return (
        path.name.endswith(".py")
        and not path.is_symlink()
        and path.is_file()
        and not is_probe_file(path)
        and not path.name.startswith(".#")
    )


def _is_source_consumer(path: Path) -> bool:
    """Read-only analysis also sees scripts and unresolved source links.

    Links are returned as uncertainty, never followed. Their caller must
    decline a proof that depends on having inspected them.
    """
    if is_probe_file(path):
        return False
    if not path.is_symlink() and not path.is_file():
        return False
    if path.suffix in (".py", ".pyw"):
        return True
    if path.suffix:
        return False
    if path.is_symlink():
        return True
    try:
        with path.open("rb") as stream:
            header = stream.readline(256)
    except OSError:
        return True
    return header.startswith(b"#!") and b"python" in header.lower()


def python_sources(
    directory: Path,
    *,
    recursive: bool = True,
    excluded: Iterable[str] = (),
    include_ignored: Optional[bool] = None,
    analysis: bool = True,
    skipped: Optional[Callable[[Path, str], None]] = None,
    include_consumers: bool = False,
) -> list[Path]:
    """Sorted regular sources, excluding build products, Git ignores and environments.

    ``analysis=False`` retains the original consumer inventory for renames:
    Git ignores and build products remain visible.

    ``include_consumers`` is for read-only proofs: also return Python shebang
    scripts, .pyw files and unresolved source links (including directory links).
    Those links must be treated as unknown consumers, never followed or edited.

    Hidden directories and ``TOOL_DIRECTORIES`` are skipped by name, an
    environment by what it holds (:func:`is_environment`), whatever its name.
    ``excluded`` names directories and files to leave out, matched by name at
    any depth below ``directory``.

    Prune directories before entering them: ignored source, especially an
    environment's incompatible fixtures, is outside both extraction and rename
    analysis. The explicitly requested root may itself have an unusual name.
    An unreadable included directory fails discovery instead of hiding sources.
    An explicitly supplied regular Python file is returned on its own.
    A missing explicit root has no sources; errors encountered within an
    existing root still fail the inventory.
    """
    is_source = _is_source_consumer if include_consumers else _is_python_source
    try:
        directory.stat()
    except FileNotFoundError:
        return []
    if directory.is_file():
        return [directory] if is_source(directory) else []
    policy = _SOURCE_POLICY.get()
    retain_ignored = (
        include_ignored
        if include_ignored is not None
        else bool(policy is not None and policy.include_ignored)
    )
    retain_ignored = retain_ignored or not analysis
    ignored = {*(ANALYSIS_DIRECTORIES if analysis else TOOL_DIRECTORIES), *excluded}
    found: list[Path] = []
    for parent, directories, filenames in os.walk(
        directory, followlinks=False, onerror=skip_unreadable
    ):
        root = Path(parent)
        if not readable_directory(root):
            directories.clear()
            continue
        if skipped is not None:
            for name in directories:
                if name in ignored or (analysis and name.endswith(".egg-info")):
                    for omitted in _omitted_sources(root / name):
                        skipped(omitted, "its directory is excluded from analysis")
        if include_consumers and recursive:
            found.extend(
                root / name
                for name in directories
                if not name.startswith(".")
                and name not in ignored
                and (root / name).is_symlink()
                and not is_environment(root / name)
            )
        directories[:] = sorted(
            name
            for name in directories
            if recursive
            and not name.startswith(".")
            and name not in ignored
            and not (analysis and name.endswith(".egg-info"))
            and not (root / name).is_symlink()
            and not is_environment(root / name)
        )
        for name in filenames:
            path = root / name
            if name not in ignored and is_source(path):
                found.append(path)
    git_ignored = frozenset() if retain_ignored else ignored_by_git(directory, found)
    if skipped is not None:
        for path in sorted(git_ignored):
            skipped(path, "Git ignores it (use --include-ignored to analyse it)")
    return sorted(path for path in found if path not in git_ignored)


def _omitted_sources(directory: Path) -> Iterator[Path]:
    """Count omitted Python files without following links or reading their contents."""
    if directory.is_symlink():
        return
    for parent, directories, names in os.walk(directory):
        directories[:] = [name for name in directories if not Path(parent, name).is_symlink()]
        for name in names:
            path = Path(parent, name)
            if _is_python_source(path):
                yield path
