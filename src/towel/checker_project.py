"""Private, coherent project copies for checkers without an in-memory overlay API.

A copy is needed because a checker must judge prospective sources without them
existing beside the user's code, and because a path excluded from the check has
to be invisible rather than merely ignored. Pyright's language server does offer
in-memory overlays, but it reanalyzes only the overlaid file: a change that
breaks a consumer reads as clean through them, which is worse than slow. A copy
the checker watches reanalyzes consumers as it would on disk.

``CheckerSnapshot`` keeps one copy alive across many checks so a warm checker can
be pointed at it repeatedly; ``checker_snapshot`` is the one-shot form.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import tomllib
from pathlib import Path
import shutil
import tempfile
from typing import Dict, Iterator, Literal, Mapping, Sequence, Tuple

from .source_files import is_probe_file
from .source_text import encode_like

ChangeKind = Literal["created", "changed", "deleted"]


@dataclass(frozen=True)
class CopyChange:
    """One file of the copy that a call to ``apply`` created, rewrote or removed."""

    path: Path
    kind: ChangeKind


_Stamp = Tuple[int, int, int, bytes]
"""What a source file looked like when it was last copied.

``(st_mtime_ns, st_size, st_ino)`` catches everything Towel itself does, since
it replaces a file atomically and the inode moves even where the new text
keeps the old size within one clock tick. It does not catch an in-place
rewrite of the same length by something else -- an editor, a formatter, a
commit hook -- inside that tick, and on a filesystem with coarse timestamps
the tick is a whole second. A verdict is worth no more than the project it was
reached against, so the digest decides and the rest is a cheap way to skip it:
hashing every checker input of Sphinx costs about four milliseconds, against a
check that costs a second.
"""


def _as_changes(changes: Mapping[Path, ChangeKind]) -> Tuple[CopyChange, ...]:
    return tuple(CopyChange(path, kind) for path, kind in sorted(changes.items()))


def _stamp(source: Path) -> _Stamp:
    status = source.stat()
    return (
        status.st_mtime_ns,
        status.st_size,
        status.st_ino,
        hashlib.blake2b(source.read_bytes(), digest_size=16).digest(),
    )


class CheckerSnapshot:
    """A private copy of one project's checker inputs, reused across checks.

    Every Python source/stub is a real copy, so imports from unchanged consumers
    see the same prospective module graph. Symlinks into the project are copied
    under their lexical names; a cycle or external source link is rejected.
    Non-code checker configuration and typing markers are preserved. Large data,
    VCS metadata, caches and virtual environments are not checker inputs.

    The copy follows the project. A candidate is a question about the project as
    it now stands, and an in-place run changes the project with every
    refactoring it applies while supplying only the files the next candidate
    alters. So each ``apply`` first brings the copy up to date with whatever
    changed on disk, and only then shows the candidate. ``revision`` counts the
    times that found something, so a caller that remembers verdicts can tell
    when the project they were reached against is gone.

    ``apply`` returns the copies it actually touched, so a caller can tell a
    watching checker exactly what moved. Sources equal to what the copy already
    holds are not rewritten, which keeps a candidate that restates the whole
    project down to the few files it alters.
    """

    def __init__(self, root: Path, *, excluded_paths: Sequence[str] = ()) -> None:
        self._root = root
        self._excluded = frozenset(Path(path).resolve() for path in excluded_paths)
        self._temporary = tempfile.TemporaryDirectory(prefix="towel-check-")
        # Copies that show a candidate's text rather than the project's.
        self._dirty: set[Path] = set()
        # For every copy made from a project file: that file, and its stamp then.
        self._sources: Dict[Path, Path] = {}
        self._stamps: Dict[Path, _Stamp] = {}
        self.revision = 0
        try:
            self._layout = _layout(Path(self._temporary.name), root)
            self.follow_project()
            self.revision = 0
        except BaseException:
            self._temporary.cleanup()
            raise

    @property
    def tree(self) -> Path:
        """The copied project root, where the checker is pointed."""
        return self._layout.target

    def path_of(self, original: str) -> Path:
        """Where ``original`` lives inside the copy."""
        return self._layout.target / Path(original).relative_to(self._root)

    def original_of(self, copied: str) -> str:
        """The project path a copied path stands for, or itself when outside."""
        path = Path(copied)
        if path.is_relative_to(self._layout.target):
            return str(self._root / path.relative_to(self._layout.target))
        return copied

    def apply(self, replacements: Mapping[str, str]) -> Tuple[CopyChange, ...]:
        """Make the copy show the project as it stands with ``replacements`` over it."""
        return self.show(replacements, after=self.follow_project())

    def follow_project(self) -> Tuple[CopyChange, ...]:
        """Copy what the project gained or changed since last asked, and drop what it lost."""
        changes: Dict[Path, ChangeKind] = {}
        current = _planned_copies(self._layout, self._excluded)
        # A copy that fails part way through has still changed, so the
        # revision advances whatever happens; a remembered verdict about the
        # copy as it was must not survive it.
        try:
            self._follow(current, changes)
        finally:
            if changes:
                self.revision += 1
        return _as_changes(changes)

    def _follow(self, current: Dict[Path, Path], changes: Dict[Path, ChangeKind]) -> None:
        for destination, source in current.items():
            stamp = _stamp(source)
            if self._stamps.get(destination) == stamp:
                continue
            changes[destination] = "changed" if destination in self._stamps else "created"
            _copy_input(source, destination, self._layout)
            self._sources[destination], self._stamps[destination] = source, stamp
            self._dirty.discard(destination)
        for destination in sorted(set(self._stamps) - set(current)):
            destination.unlink(missing_ok=True)
            changes[destination] = "deleted"
            del self._stamps[destination], self._sources[destination]
            self._dirty.discard(destination)

    def show(
        self, replacements: Mapping[str, str], *, after: Sequence[CopyChange] = ()
    ) -> Tuple[CopyChange, ...]:
        """Put ``replacements`` over the copy, and nothing from a previous call.

        ``after`` is what ``follow_project`` just reported; the result accounts
        for both, so a file the project created and the candidate then rewrote is
        still reported as created.
        """
        changes: Dict[Path, ChangeKind] = {change.path: change.kind for change in after}
        wanted = {self.path_of(name): source for name, source in replacements.items()}
        for stale in sorted(self._dirty - set(wanted)):
            # The previous candidate's text; the project's own comes back, or
            # nothing does when that candidate invented the file.
            source = self._sources.get(stale)
            if source is None:
                stale.unlink(missing_ok=True)
                changes[stale] = "deleted"
            else:
                _copy_input(source, stale, self._layout)
                changes.setdefault(stale, "changed")
            self._dirty.discard(stale)
        for output, text in sorted(wanted.items()):
            source = self._sources.get(output)
            content = encode_like(source.read_bytes() if source is not None else b"", text)
            existed = output.is_file()
            if existed and output.read_bytes() == content:
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
            self._dirty.add(output)
            if existed or changes.get(output) == "deleted":
                changes.setdefault(output, "changed")
                if changes[output] == "deleted":
                    changes[output] = "changed"
            else:
                changes[output] = "created"
        return _as_changes(changes)

    def close(self) -> None:
        self._temporary.cleanup()


@dataclass(frozen=True)
class _Layout:
    """Where a project and the configuration it extends sit inside its copy."""

    root: Path
    target: Path
    tree: Path
    common: Path
    configs: Tuple[Path, ...]

    def copy_of(self, config: Path) -> Path:
        return self.tree / config.relative_to(self.common)


def _layout(temporary: Path, root: Path) -> _Layout:
    """An empty copy of ``root`` under ``temporary``, placed so its extends chain resolves.

    Shared monorepo base configs retain their relative locations, so the chain
    resolves in the copy exactly as it does in the original project.
    """
    configs = _pyright_config_inputs(root)
    common = Path(os.path.commonpath([str(root), *(str(path.parent) for path in configs)]))
    tree = temporary / "project"
    target = tree / root.relative_to(common)
    target.mkdir(parents=True)
    return _Layout(root, target, tree, common, configs)


def _planned_copies(layout: _Layout, excluded: frozenset[Path]) -> Dict[Path, Path]:
    """Every copy the project calls for right now, mapped to the file it copies."""
    plan = {
        destination: source
        for source, destination in _inputs(
            layout.root, layout.target, layout.root, frozenset(), excluded
        )
    }
    plan.update({layout.copy_of(config): config for config in layout.configs})
    return plan


def _copy_input(source: Path, destination: Path, layout: _Layout) -> None:
    """Copy one checker input, pointing any absolute project path it holds into the copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source in layout.configs:
        rebased = {str(layout.root): str(layout.target)}
        rebased.update({str(config): str(layout.copy_of(config)) for config in layout.configs})
        text = source.read_text(encoding="utf-8-sig")
        for original_prefix, replacement_prefix in sorted(
            rebased.items(), key=lambda item: -len(item[0])
        ):
            text = text.replace(original_prefix, replacement_prefix)
        destination.write_text(text, encoding="utf-8")
        return
    shutil.copyfile(source, destination)
    if source.suffix in {".toml", ".json", ".ini", ".cfg"}:
        # Absolute project search paths must resolve inside this same
        # prospective graph, just like relative paths already do.
        try:
            text = destination.read_text(encoding="utf-8")
        except UnicodeError:
            # Its absolute paths cannot be rewritten, so they would point out
            # of the copy at the real tree. Better no configuration than one
            # that sends the checker somewhere else.
            raise ValueError(f"Cannot rebase a checker configuration that is not UTF-8: {source}")
        destination.write_text(text.replace(str(layout.root), str(layout.target)), encoding="utf-8")


@contextmanager
def checker_snapshot(
    root: Path, replacements: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
) -> Iterator[Path]:
    """One copy of ``root`` showing ``replacements``, removed when the block ends."""
    snapshot = CheckerSnapshot(root, excluded_paths=excluded_paths)
    try:
        snapshot.apply(replacements)
        yield snapshot.tree
    finally:
        snapshot.close()


def _inputs(
    root: Path, target: Path, project: Path, ancestors: frozenset[Path], excluded: frozenset[Path]
) -> Iterator[Tuple[Path, Path]]:
    """Each checker input under ``root`` with the place its copy belongs."""
    resolved = root.resolve()
    if resolved in ancestors or not resolved.is_relative_to(project):
        raise ValueError(f"Cannot snapshot cyclic or external source directory: {root}")
    ancestry = ancestors | {resolved}
    for entry in sorted(root.iterdir()):
        if is_probe_file(entry) or entry.resolve() in excluded:
            continue
        destination = target / entry.name
        if entry.is_dir():
            if (
                entry.name
                in {
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
                or (entry / "pyvenv.cfg").is_file()
            ):
                continue
            yield from _inputs(entry, destination, project, ancestry, excluded)
        elif entry.is_file() and (
            entry.suffix in {".py", ".pyi", ".toml", ".json", ".ini", ".cfg"}
            or entry.name in {"py.typed", ".gitignore"}
        ):
            if entry.is_symlink() and not entry.resolve().is_relative_to(project):
                raise ValueError(f"Cannot snapshot an external source link: {entry}")
            yield entry, destination


def _json_config(text: str) -> dict[str, object]:
    """Read pyright's JSON-with-comments without treating string contents as syntax."""
    cleaned: list[str] = []
    index = 0
    quoted = False
    while index < len(text):
        char = text[index]
        if quoted:
            cleaned.append(char)
            if char == "\\" and index + 1 < len(text):
                index += 1
                cleaned.append(text[index])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            cleaned.append(char)
        elif text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            cleaned.append("\n")
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unterminated comment in pyright configuration")
            cleaned.append(" ")
            index = end + 2
            continue
        elif char == "," and text[index + 1 :].lstrip().startswith(("}", "]")):
            pass
        else:
            cleaned.append(char)
        index += 1
    # Remove trailing commas after comments have been stripped, still respecting
    # strings. The first pass handles plain trailing commas; this pass handles
    # a comma followed by a comment before the closing bracket.
    without_comments = "".join(cleaned)
    if without_comments != text:
        return _json_config(without_comments)
    value = json.loads(without_comments)
    if not isinstance(value, dict):
        raise ValueError("Pyright configuration must be an object")
    return {str(key): item for key, item in value.items()}


def _pyright_config_inputs(root: Path) -> tuple[Path, ...]:
    """The extends chain; external checker source roots must not be silently omitted."""
    initial = root / "pyrightconfig.json"
    if initial.is_file():
        data = _json_config(initial.read_text(encoding="utf-8-sig"))
    else:
        initial = root / "pyproject.toml"
        if not initial.is_file():
            return ()
        with initial.open("rb") as handle:
            project = tomllib.load(handle)
        tool = project.get("tool", {})
        data = tool.get("pyright", {}) if isinstance(tool, dict) else {}
        if not isinstance(data, dict):
            raise ValueError("Invalid pyright TOML configuration")
    found: list[Path] = [initial]
    while True:
        # A path in a shared base config still resolves relative to that base.
        # Only paths inside the copied project are representable here. Refuse
        # others rather than treating their absent stubs/imports as unknown.
        _check_config_source_paths(data, initial.parent, root)
        base = data.get("extends")
        if base is None:
            return tuple(found)
        if not isinstance(base, str):
            raise ValueError("Pyright extends must name one configuration file")
        initial = (initial.parent / base).resolve()
        if initial in found:
            raise ValueError("Cyclic pyright configuration extends")
        found.append(initial)
        data = _json_config(initial.read_text(encoding="utf-8-sig"))


def _check_config_source_paths(data: Mapping[str, object], directory: Path, root: Path) -> None:
    for key in ("stubPath", "typeshedPath", "extraPaths", "include", "executionEnvironments"):
        value = data.get(key)
        if value is None:
            continue
        if key == "executionEnvironments":
            if not isinstance(value, list):
                raise ValueError("Invalid pyright execution environments")
            for environment in value:
                if not isinstance(environment, dict):
                    raise ValueError("Invalid pyright execution environment")
                nested: dict[str, object] = {str(k): v for k, v in environment.items()}
                if "root" in nested:
                    nested["include"] = [nested["root"]]
                _check_config_source_paths(nested, directory, root)
            continue
        paths = value if isinstance(value, list) else [value]
        for path in paths:
            if not isinstance(path, str):
                raise ValueError(f"Invalid pyright {key} path")
            # Nonexistent default paths are harmless; a configured external
            # source/stub directory would otherwise silently read stale files.
            literal = path.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
            resolved = (directory / literal).resolve()
            if not resolved.is_relative_to(root):
                raise ValueError(f"External pyright {key} path cannot be snapshotted: {resolved}")
