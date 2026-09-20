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
import json
import os
import tomllib
from pathlib import Path
import shutil
import tempfile
from typing import Dict, Iterator, Mapping, Sequence, Tuple

from .source_files import is_probe_file
from .source_text import encode_like


class CheckerSnapshot:
    """A private copy of one project's checker inputs, reused across checks.

    Every Python source/stub is a real copy, so imports from unchanged consumers
    see the same prospective module graph. Symlinks into the project are copied
    under their lexical names; a cycle or external source link is rejected.
    Non-code checker configuration and typing markers are preserved. Large data,
    VCS metadata, caches and virtual environments are not checker inputs.

    ``apply`` writes a candidate's prospective sources and returns the copies it
    actually changed, so a caller can tell a watching checker exactly what moved.
    Sources equal to what the copy already holds are not rewritten, which keeps a
    candidate that restates the whole project down to the few files it alters.
    """

    def __init__(self, root: Path, *, excluded_paths: Sequence[str] = ()) -> None:
        self._root = root
        self._temporary = tempfile.TemporaryDirectory(prefix="towel-check-")
        self._dirty: set[Path] = set()
        # What each replaced file held before the first candidate touched it.
        # Restoring from here rather than from the project keeps the copy a
        # fixed base, so a candidate's verdict depends on the candidate alone,
        # even when the project is being refactored in place.
        self._pristine: Dict[Path, bytes] = {}
        try:
            self._tree, self._target = _populate(Path(self._temporary.name), root, excluded_paths)
        except BaseException:
            self._temporary.cleanup()
            raise

    @property
    def tree(self) -> Path:
        """The copied project root, where the checker is pointed."""
        return self._target

    def path_of(self, original: str) -> Path:
        """Where ``original`` lives inside the copy."""
        return self._target / Path(original).relative_to(self._root)

    def original_of(self, copied: str) -> str:
        """The project path a copied path stands for, or itself when outside."""
        path = Path(copied)
        if path.is_relative_to(self._target):
            return str(self._root / path.relative_to(self._target))
        return copied

    def apply(self, replacements: Mapping[str, str]) -> Tuple[Path, ...]:
        """Make the copy show ``replacements``, and nothing from a previous call."""
        wanted = {self.path_of(name): source for name, source in replacements.items()}
        changed: list[Path] = []
        for stale in sorted(self._dirty - set(wanted)):
            if self._restore(stale):
                changed.append(stale)
        self._dirty -= set(changed)
        for output, source in sorted(wanted.items()):
            if output not in self._pristine:
                self._pristine[output] = output.read_bytes() if output.is_file() else b""
            content = encode_like(self._pristine[output], source)
            if output.is_file() and output.read_bytes() == content:
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
            self._dirty.add(output)
            changed.append(output)
        return tuple(changed)

    def _restore(self, copied: Path) -> bool:
        """Put one copied file back to the base the copy was made from."""
        data = self._pristine.get(copied)
        if data is None:
            return False
        if not data and not (self._root / copied.relative_to(self._target)).is_file():
            copied.unlink(missing_ok=True)
            return True
        if copied.is_file() and copied.read_bytes() == data:
            return False
        copied.write_bytes(data)
        return True

    def close(self) -> None:
        self._temporary.cleanup()


def _populate(temporary: Path, root: Path, excluded_paths: Sequence[str]) -> Tuple[Path, Path]:
    """Copy ``root``'s checker inputs under ``temporary``; return the tree and project."""
    configs = _pyright_config_inputs(root)
    common = Path(os.path.commonpath([str(root), *(str(path.parent) for path in configs)]))
    tree = temporary / "project"
    target = tree / root.relative_to(common)
    target.mkdir(parents=True)
    _copy_inputs(
        root, target, root, frozenset(), frozenset(Path(p).resolve() for p in excluded_paths)
    )
    # Shared monorepo base configs retain their relative locations, so an
    # extends chain resolves exactly as it does in the original project.
    rebased = {str(root): str(target)}
    rebased.update({str(path): str(tree / path.relative_to(common)) for path in configs})
    for config in configs:
        destination = tree / config.relative_to(common)
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = config.read_text(encoding="utf-8-sig")
        for original_prefix, replacement_prefix in sorted(
            rebased.items(), key=lambda item: -len(item[0])
        ):
            text = text.replace(original_prefix, replacement_prefix)
        destination.write_text(text, encoding="utf-8")
    return tree, target


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


def _copy_inputs(
    root: Path, target: Path, project: Path, ancestors: frozenset[Path], excluded: frozenset[Path]
) -> None:
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
            destination.mkdir()
            _copy_inputs(entry, destination, project, ancestry, excluded)
        elif entry.is_file() and (
            entry.suffix in {".py", ".pyi", ".toml", ".json", ".ini", ".cfg"}
            or entry.name in {"py.typed", ".gitignore"}
        ):
            if entry.is_symlink() and not entry.resolve().is_relative_to(project):
                raise ValueError(f"Cannot snapshot an external source link: {entry}")
            shutil.copyfile(entry, destination)
            if entry.suffix in {".toml", ".json", ".ini", ".cfg"}:
                # Absolute project search paths must resolve inside this same
                # prospective graph, just like relative paths already do.
                try:
                    text = destination.read_text(encoding="utf-8")
                except UnicodeError:
                    continue
                snapshot_root = target
                for _ in root.relative_to(project).parts:
                    snapshot_root = snapshot_root.parent
                destination.write_text(
                    text.replace(str(project), str(snapshot_root)), encoding="utf-8"
                )


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
