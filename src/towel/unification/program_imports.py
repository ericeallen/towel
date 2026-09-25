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

"""The program's import model, asked in the paths a run works on.

Every import Towel writes, and every question about what an import runs, is
answered by the model of the program's own imports (``towel.import_model``;
docs/DECISIONS.md, "Import names come from the program"). A run refactors a
stage, a private copy of the whole project at the same relative places, but
the model is read from the project itself: the interpreter probe that tells
an installed copy of a name from the project's own must see the project where
it lives, since an editable install puts that place, and not the stage, on
``sys.path``. So each question is mapped from the stage to the project
before it is asked (:meth:`ProgramImports.origin`), and a file the answer
names is read from the stage, which holds what the run has written
(:meth:`ProgramImports.in_run`).

The model reads nothing in a directory the run excludes (``--exclude``), so
it cannot say what an import that enters one runs; nor in the few
directories every scan skips, nor behind a symbolic link.
:meth:`ProgramImports.reached` says so rather than answer from what it could
see.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterable, Optional, Sequence, Tuple

from ..consumers import SKIPPED_DIRECTORIES, ScanLimitExceeded
from ..import_model import ImportModel, ImportSpelling, NameStatus, build_import_model
from .exceptions import ProjectScanLimitError

_UNSEEN_EVERYWHERE = frozenset(
    name for name in SKIPPED_DIRECTORIES - {"build", "dist"} if name.isidentifier()
)
"""Directories the model never reads that an import could still name: ``node_modules``."""


@dataclass(frozen=True)
class ProgramImports:
    """The run's import model, and where the run works when that is a copy of the model's tree."""

    model: ImportModel
    excluded_names: FrozenSet[str] = frozenset()
    """Directory names the run leaves out, which the model therefore never read."""
    stage_root: Optional[Path] = None
    """The resolved root of the stage the run refactors, a copy of ``model.root``; None outside a run."""

    def origin(self, path: Path) -> Path:
        """``path`` as it stands in the project the model was read from."""
        resolved = path.resolve()
        stage = self.stage_root
        if stage is not None and (resolved == stage or resolved.is_relative_to(stage)):
            return self.model.root / resolved.relative_to(stage)
        return resolved

    def in_run(self, path: Path) -> Path:
        """The file the run reads for ``path`` of the project: its staged copy, where there is one.

        The stage holds the project's sources, rewritten as the run goes; a
        file it does not hold is read where it is, unchanged.
        """
        stage = self.stage_root
        root = self.model.root
        if stage is None or not path.is_relative_to(root):
            return path
        staged = stage / path.relative_to(root)
        return staged if staged.exists() else path

    def spelling(self, importer: Path, provider: Path) -> Optional[ImportSpelling]:
        """How ``importer`` may name ``provider``, or None when the program shows no way that works."""
        return self.model.spelling(self.origin(importer), self.origin(provider))

    def module_name(self, path: Path) -> Optional[str]:
        """The absolute name the program's imports give ``path``, when a trusted name reaches it."""
        return self.model.module_name(self.origin(path))

    def leaves_unchanged(self, path: Path) -> bool:
        """Whether a run must leave ``path`` as it is: it imports a module the tree lacks.

        Such a file is broken, or runs only where something the tree does not
        show supplies the module: a mock around sphinx's test data, or the
        ``_version.py`` a build generates. Nothing shows a change to it keeps
        it working, so it hosts no helper, borrows none, and gets no helper of
        its own either.
        """
        return self.origin(path) in self.model.importers_of_missing_modules

    def is_local(self, name: str) -> bool:
        """Whether the top-level ``name`` may be one of the project's modules rather than an installed one."""
        info = self.model.names.get(name)
        return info is not None and info.status is not NameStatus.EXTERNAL

    def reached(
        self, importer: Path, level: int, module: Optional[str], names: Sequence[str] = ()
    ) -> Optional[FrozenSet[Path]]:
        """The project files an import in ``importer`` may execute, in the model's paths.

        None when the import may enter a directory the model did not read
        (``excluded_names``, the directories every scan skips, a symbolic
        link), whose files it may execute unseen: pip's ``_vendor`` left out
        of a run is still what ``import pip._vendor.rich`` runs.
        """
        source = self.origin(importer)
        if self._may_enter_unread(source, level, module, names):
            return None
        return self.model.files_reached(source, level, module, names)

    def package_initializers(self, module: Path) -> Tuple[Path, ...]:
        """The ``__init__.py`` of each package enclosing ``module``, innermost first, in the model's paths.

        They are the initializers importing ``module`` runs: up to the top of
        the package the program imports it as part of (the model's context),
        and none for a module that is in no package.
        """
        source = self.origin(module)
        context = self.model.context_of(source)
        if context is None or context == source:
            return ()
        initializers = []
        directory = source.parent
        while directory == context or directory.is_relative_to(context):
            initializer = directory / "__init__.py"
            if initializer != source and initializer.is_file():
                initializers.append(initializer)
            if directory == context or directory.parent == directory:
                break
            directory = directory.parent
        return tuple(initializers)

    def _may_enter_unread(
        self, importer: Path, level: int, module: Optional[str], names: Sequence[str]
    ) -> bool:
        """Whether the import names a directory the model did not read, on its way or at its end."""
        unread = self.excluded_names | _UNSEEN_EVERYWHERE
        parts = module.split(".") if module else []
        bases: Tuple[Path, ...]
        if level:
            base = _climbed(importer.parent, level - 1, self.model.root)
            bases = () if base is None else (base,)
        elif not parts:
            return False
        elif parts[0] in unread:
            return True  # A top-level directory of the name may be the one it finds.
        else:
            info = self.model.names.get(parts[0])
            bases = info.candidates if info is not None else ()
            parts = parts[1:]
        return any(_enters(base, parts, names, unread) for base in bases)


def _climbed(directory: Path, steps: int, root: Path) -> Optional[Path]:
    """``directory`` after ``steps`` parents, or None when that leaves ``root``."""
    for _ in range(steps):
        if directory == root:
            return None
        directory = directory.parent
    return directory


def _enters(base: Path, parts: Sequence[str], names: Sequence[str], unread: FrozenSet[str]) -> bool:
    """Whether walking ``parts`` from ``base``, then naming ``names`` there, enters an unread directory."""
    cursor = base
    for part in parts:
        if _unread(cursor / part, unread):
            return True
        cursor = cursor / part
        if not cursor.is_dir():
            return False
    return any(_unread(cursor / name, unread) for name in names)


def _unread(directory: Path, unread: FrozenSet[str]) -> bool:
    return (directory.name in unread or directory.is_symlink()) and directory.is_dir()


def program_imports(
    root: Path, excluded_names: Iterable[str] = (), *, stage_root: Optional[Path] = None
) -> ProgramImports:
    """The model of the project at ``root``, less the directories named ``excluded_names``.

    A tree too large to read whole stops the run, as every whole-project
    read does: a model of part of it could miss the second copy that makes a
    name ambiguous.
    """
    excluded = frozenset(excluded_names)
    try:
        model = build_import_model(root, excluded_names=excluded)
    except ScanLimitExceeded as error:
        raise ProjectScanLimitError(
            f"{error}. Towel reads the project from the nearest directory with a"
            " pyproject.toml, setup.cfg or setup.py; give the code one, or move it out of the"
            " larger tree"
        ) from error
    return ProgramImports(model, excluded, None if stage_root is None else stage_root.resolve())
