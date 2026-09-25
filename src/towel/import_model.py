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

"""The names a program's own imports give its modules, and how one module may import another.

A module's absolute name is not a property of its file. ``src/alpha/a.py`` is
``alpha.a`` in an installed wheel, ``src.alpha.a`` to a test run from the
project root when ``src/__init__.py`` exists, and nothing a script beside it
can import. Towel used to choose one of these from packaging metadata or from
the ``__init__`` chain, and every wrong choice was an import that broke an
installed package: ``src.waitress.task``, ``foo.src.foo.a``, ``src.alpha.a``.
The program's own imports already work wherever the program runs, so this
module takes names from them and from nothing else (docs/DECISIONS.md, "Import
names come from the program").

The model is built once from a project's Python files:

- Every absolute import contributes its top-level name. A name's *candidates*
  are the directories (regular or namespace) holding Python files, and the
  ``.py`` files, of that name that are not inside a regular package. The name
  is *attested* at its location when it has exactly one candidate, no module
  outside the project provides it to this interpreter, and no distribution the
  project requires is named like it; *ambiguous* when any of these fails; and
  *external* when it has no candidate at all.
- The imports are checked, and each problem is a record, never an exception:
  every ambiguous name; an import that runs, of an attested name, that its
  location does not hold; a location reachable under two names; a relative
  import that climbs out of its package, or that runs and names a missing
  module. A name whose location
  a problem leaves in doubt is not trusted, and nothing is ever spelled into
  it; a file holding a problem's import is never a provider and is given no
  new import.
- Each module belongs to a *context*: its top-level package, or itself when it
  is in none. A context attests the trusted names its runtime imports use.

:meth:`ImportModel.spelling` then answers how one module may import another:
relatively within a package (unless the importing file spells its own package
absolutely), absolutely across packages only when the importing context
already imports the other package, and not at all otherwise.

The rules as first stated reproduce some of the defects they were written to
end, and are refined where they do; each refinement is argued where it is made:

- A stray ``__init__.py`` does not make a package. A top-level package that no
  import uses as a package is a plain directory, so ``src/__init__.py`` neither
  hides ``src/alpha`` from the tests' ``import alpha`` nor lets ``src/alpha``
  reach ``src/beta`` as ``..beta`` (:func:`_relaxed_candidates`,
  :func:`_package_of`).
- Import itself decides some ambiguities: a built-in or frozen module cannot
  be displaced, and a regular package or module anywhere on the path displaces
  every namespace directory of its name (:func:`_classify`). A candidate that
  holds none of the modules its name's imports need is a namesake, not the
  name (:func:`_checked`), and a module that registers submodules at run time
  (``six.moves``) is not missing them (:meth:`_Listings.submodules`).
- A candidate that does hold one may still be a namesake. A project requiring
  ``click`` imports that distribution under the name wherever it is installed,
  so a directory ``click/`` sharing one module with the library is in doubt
  even where this interpreter lacks the library (:func:`_required_elsewhere`).
  So is one the program imports a module of that it lacks, from outside it:
  ``third_party/click/`` holding ``utils.py`` is not the ``click`` whose
  ``click.core`` the program also imports. The directory's own import of a
  module it lacks, or the project being the distribution of that name,
  says nothing of another copy (:func:`_lacking_elsewhere`).
  An environment inside the root, the project's own ``.venv``, is outside the
  project: what is installed there is a distribution, not the tree
  (:func:`_in_installation`).
- An import of a module a name's location lacks says nothing about where the
  name lives. sphinx's test data imports ``sphinx.missing_module4``, which its
  tests mock, and every other import of ``sphinx`` still places it; so only
  the file making that import is in doubt, and no file is the missing module
  for any import to name (:class:`UnresolvedImport`). An initializer making
  one blocks the modules below it only for importers not already imported
  through it (:meth:`ImportModel._blocked_provider`).
- An import attests a name only when it runs, runs unguarded, and runs where
  ``sys.path`` is not being changed (:attr:`ImportSite.attests`), and only
  such an import locates a name or puts one in doubt (:func:`_places`).
- A new import may enter a directory only where the importer's context or the
  provider's own package already imports from it, the owner's rule for
  top-level packages applied at every level, because part of a package may
  not ship: ``bs4/tests`` is left out of beautifulsoup4's wheel
  (:meth:`ImportModel._only_entered_directories`). Only an import that runs
  whenever its file is imported counts, never one inside a function
  (:attr:`ImportSite.loads`). A build can also leave out one module of a
  directory it ships, and a module the project's build configuration
  declares left out of what ships is never a provider to one it keeps
  (:meth:`ImportModel._ships_wherever`). A package's ``__main__`` is never a
  provider, and neither is a module below a directory without
  ``__init__.py`` inside a regular package (:func:`_absolute_module`).

What the model assumes, and cannot check:

- the program's existing imports work in every environment it is used in, and
  its packages are imported from this tree. An installed copy this
  interpreter can see makes the name ambiguous, wherever it is installed, the
  project's own ``.venv`` included, and so does a distribution the project
  declares it requires. One that only the project's own environment holds,
  and that no declaration names by its import name, is invisible here, so the
  interpreter Towel runs in stands for the project's (as it already does for
  the type checker);
- a location holding any module its name's imports need is that name, unless
  one of those says otherwise, or an import from outside it needs one it
  lacks. So where this interpreter lacks the library, a directory named like
  it is taken for it only where the program imports nothing of the name the
  directory lacks, and the project requires the library under a different
  distribution name (``PyYAML`` provides ``yaml``), reaches it only as a
  dependency's dependency with no lockfile recording it, or installs it from
  a recipe not read (``tox.ini``, a noxfile, a CI file, a setup.py); and
  where the project's metadata names the project itself so, a module the
  directory lacks is taken to be missing from the project, not held by
  another copy;
- what a build leaves out is what its configuration declares, as read by
  :mod:`towel.shipped_files`, and what no import from outside a directory
  shows ships: a module a setup.py or a build hook leaves out of a directory
  the program imports from would pass for shipped;
- a module inside a regular package is imported through that package, never
  run by its path with its own directory on ``sys.path``, unless it imports
  by a top-level name a module that only its package holds;
- ``sys.path`` is the same when a module is imported as when its functions
  run, except in files that change it, whose imports attest nothing;
- dynamic imports (``importlib.import_module``, ``__import__``) name nothing,
  and only ``.py`` files are modules: stubs and compiled extensions count only
  as evidence that a module an import names exists.
"""

from __future__ import annotations

import ast
import enum
import functools
import importlib.machinery
import os
import sys
import sysconfig
import warnings
from dataclasses import dataclass, field, replace
from keyword import iskeyword
from pathlib import Path
from typing import (
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .consumers import MAXIMUM_FILES, SKIPPED_DIRECTORIES, ScanLimitExceeded
from .declared_requirements import Requirement, declared_requirements, normalized_name, own_names
from .shipped_files import Artifact, left_out

__all__ = [
    "AmbiguousName",
    "Doubt",
    "FileUnderTwoNames",
    "ImportModel",
    "ImportProblem",
    "ImportSite",
    "ImportSpelling",
    "InstalledProbe",
    "MissingModule",
    "NameStatus",
    "OutsideProvider",
    "ProviderKind",
    "RelativeImportEscapes",
    "RelativeImportMissing",
    "SpellingBasis",
    "TopLevelInsidePackage",
    "TopLevelName",
    "UnresolvedImport",
    "build_import_model",
    "installed_outside",
]


class ProviderKind(enum.Enum):
    """What an interpreter found for a top-level name outside the project.

    The kind decides which of the project's candidates it can displace.
    Import searches ``sys.path`` in order, but a regular package or a module
    anywhere on it beats every namespace directory of the same name, and a
    built-in or frozen module is found before ``sys.path`` is searched at all.
    """

    UNSHADOWABLE = "built in or frozen"
    """Found before any path entry: no project file can be imported under its name."""
    MODULE = "a module or regular package"
    """Displaces the project's namespace directories of the name, and competes with the rest."""
    NAMESPACE = "namespace package portions"
    """Merges with the project's namespace directories, so the name spans both."""
    UNKNOWN = "a lookup that failed"
    """Nothing is known, so nothing in the project can be relied on to be the name."""


@dataclass(frozen=True)
class OutsideProvider:
    """A provider of a top-level name outside the project, and where it is."""

    kind: ProviderKind
    description: str


InstalledProbe = Callable[[str, Path], Optional[OutsideProvider]]
"""What provides a top-level name outside a project root, or ``None`` when nothing does."""


# -- What an import says ------------------------------------------------------


@dataclass(frozen=True)
class ImportSite:
    """One import as written: ``level`` dots, then ``module``, naming ``names`` from it.

    ``import a.b`` is ``(0, "a.b", ())``; each alias of ``import a, b`` is a
    site of its own. The flags say when the statement runs: never, under
    ``TYPE_CHECKING`` (``runtime`` is false); expecting it may fail, inside
    ``try``/``except ImportError`` or ``suppress(ImportError)`` (``guarded``);
    or only when an enclosing function is called (``deferred``).
    """

    file: Path
    line: int
    level: int
    module: Optional[str]
    names: Tuple[str, ...]
    runtime: bool = True
    guarded: bool = False
    deferred: bool = False

    @property
    def top_level(self) -> Optional[str]:
        """The first component of an absolute import's module; ``None`` for a relative one."""
        if self.level or not self.module:
            return None
        return self.module.partition(".")[0]

    @property
    def attests(self) -> bool:
        """Whether this import shows its top-level name is importable wherever the file runs."""
        return self.level == 0 and self.runtime and not self.guarded

    @property
    def loads(self) -> bool:
        """Whether it runs whenever its file is imported: at import time, unguarded, not in a function.

        Only such an import shows that what it names is present wherever the
        file is; one inside a function may run only on a path, a developer's
        command say, that the installed program never takes.
        """
        return self.runtime and not self.guarded and not self.deferred

    def statement(self) -> str:
        """The import as source, for messages."""
        if self.level == 0 and not self.names:
            return f"import {self.module}"
        return f"from {'.' * self.level}{self.module or ''} import {', '.join(self.names)}"

    def where(self, root: Path) -> str:
        """``path:line`` with the path relative to ``root`` where it can be."""
        return f"{_shown(self.file, root)}:{self.line}"


class NameStatus(enum.Enum):
    """What the project's tree says a top-level name its imports use refers to."""

    ATTESTED = "attested"
    """Exactly one location in the project, and no provider outside it."""
    AMBIGUOUS = "ambiguous"
    """Several locations in the project, or one and a provider outside it or a distribution it requires."""
    EXTERNAL = "external"
    """No location in the project: the standard library or an installed distribution."""


@dataclass(frozen=True)
class TopLevelName:
    """A top-level name the program imports, where it may live, and whether it can be trusted."""

    name: str
    status: NameStatus
    candidates: Tuple[Path, ...]
    """Every location in the project that could be this name, sorted."""
    installed: Optional[str] = None
    """What this interpreter would import instead, when something outside the project provides it."""
    required: Optional[str] = None
    """A requirement the project declares on a distribution of this name, which installed provides it."""
    lacking: Optional[UnresolvedImport] = None
    """An import, from outside the name's one location, of a module of the name that location lacks."""
    flagged: bool = False
    """Whether a problem leaves the name's location in doubt; a flagged name is never spelled into."""

    @property
    def location(self) -> Optional[Path]:
        """The one location an attested name has."""
        return self.candidates[0] if self.status is NameStatus.ATTESTED else None

    @property
    def trusted(self) -> bool:
        """Attested, and involved in no problem: imports of it may be written and relied on."""
        return self.status is NameStatus.ATTESTED and not self.flagged


# -- Problems -----------------------------------------------------------------


class Doubt(enum.Enum):
    """Where a problem's doubt about a name comes from, which decides what can resolve it."""

    TREE = "the project's tree"
    """A second copy of the name, or an import that names a module two ways or climbs out of it."""
    INSTALLED = "a copy installed outside the project"
    """A provider the interpreter can import, which no ``--exclude`` reaches."""
    REQUIRED = "a distribution the project requires"
    """A distribution of the name, which is what the installed project imports by it."""
    LACKING = "a module the tree's copy lacks"
    """An import of a module of the name that its one location lacks: another copy holds it."""


@dataclass(frozen=True)
class AmbiguousName:
    """A name the program imports that could be more than one module.

    Which one an import finds depends on ``sys.path`` in the environment that
    runs it, which the tree does not show: ``src/alpha`` beside a stale
    ``build/lib/alpha``, or a project directory named like an installed
    distribution.
    """

    name: str
    candidates: Tuple[Path, ...]
    installed: Optional[str]
    required: Optional[str] = None
    """A requirement the project declares on a distribution of the name, as written and where."""
    lacking: Optional[UnresolvedImport] = None
    """An import, from outside the name's one location, of a module of the name it lacks."""

    @property
    def names_in_doubt(self) -> FrozenSet[str]:
        return frozenset({self.name})

    @property
    def found_at(self) -> Tuple[Path, ...]:
        return self.candidates

    @property
    def doubts(self) -> FrozenSet[Doubt]:
        return frozenset(
            doubt
            for doubt, present in (
                (Doubt.TREE, len(self.candidates) > 1),
                (Doubt.INSTALLED, self.installed is not None),
                (Doubt.REQUIRED, self.required is not None),
                (Doubt.LACKING, self.lacking is not None),
            )
            if present
        )

    def describe(self, root: Path) -> str:
        places = [_shown(candidate, root) for candidate in self.candidates]
        if self.installed is not None:
            places.append(f"outside the project ({self.installed})")
        if self.required is not None:
            places.append(f"the distribution the project requires ({self.required})")
        if self.lacking is not None:
            site = self.lacking.site
            places.append(
                f"another {self.name}, holding the {self.lacking.missing} that"
                f" {site.where(root)} imports ({site.statement()}),"
                f" which {_shown(self.lacking.location, root)} lacks"
            )
        return f"{self.name} could be any of: {'; '.join(places)}"


@dataclass(frozen=True)
class UnresolvedImport:
    """An import that runs, of an attested name, naming a module that the name's one location does not hold.

    The import is broken, or what it names exists only where it runs:
    sphinx's test data imports ``sphinx.missing_module4``, which its tests
    mock, and a generated ``_version.py`` is missing from a fresh clone.
    Either way it says nothing about where the name lives, which the name's
    imports that do resolve still attest; a name none of whose imports
    resolve is a namesake, and external (:func:`_checked`). So it leaves no
    name in doubt. It is one only where the location's own file makes it,
    or the project is the distribution of the name; anywhere else, the
    import shows another copy of the name, and the name is in doubt
    (:func:`_lacking_elsewhere`). The file making it is neither a provider nor given a new
    import, and no file is the missing module, so no import is ever spelled
    into it.
    """

    site: ImportSite
    name: str
    location: Path
    missing: str

    @property
    def names_in_doubt(self) -> FrozenSet[str]:
        return frozenset()

    @property
    def doubts(self) -> FrozenSet[Doubt]:
        return frozenset()

    @property
    def found_at(self) -> Tuple[Path, ...]:
        return (self.site.file,)

    @property
    def from_inside(self) -> bool:
        """Whether the file lies in the package it names a module of, as its own ``_version`` import does."""
        return self.site.file.is_relative_to(self.location)

    def describe(self, root: Path) -> str:
        return (
            f"{self.site.where(root)}: {self.site.statement()} needs {self.missing},"
            f" which {_shown(self.location, root)} does not hold"
        )


@dataclass(frozen=True)
class FileUnderTwoNames:
    """A location reachable under two of the program's top-level names, so it can load twice.

    ``src.alpha.a`` and ``alpha.a`` are two module objects with two copies of
    every global; a helper imported under one name is not the other's.
    """

    location: Path
    names: Tuple[str, str]

    @property
    def names_in_doubt(self) -> FrozenSet[str]:
        return frozenset(name.partition(".")[0] for name in self.names)

    @property
    def doubts(self) -> FrozenSet[Doubt]:
        return frozenset({Doubt.TREE})

    @property
    def found_at(self) -> Tuple[Path, ...]:
        return (self.location,)

    def describe(self, root: Path) -> str:
        first, second = self.names
        return f"{_shown(self.location, root)} is reachable both as {first} and as {second}"


@dataclass(frozen=True)
class TopLevelInsidePackage:
    """A top-level name found only inside a package the program also uses as a package.

    ``import alpha.a`` from the tests finds ``src/alpha`` only with ``src`` on
    ``sys.path``, while another import uses ``src`` itself as a package, so the
    same files are ``alpha.a`` to one and ``src.alpha.a`` to the other. A module
    file is the same: ``pkg/c.py`` importing ``helpers_top``, which only
    ``pkg/helpers_top.py`` provides, runs with ``pkg`` itself on ``sys.path``,
    as the top-level module ``c``, and a relative import written into it fails
    there (:func:`_modules_beside_their_importers`).
    """

    name: str
    location: Path
    package: Path
    site: Optional[ImportSite] = field(default=None, compare=False)
    """The import naming it top-level, which ``--exclude`` of its directory, or a fix, removes."""

    @property
    def names_in_doubt(self) -> FrozenSet[str]:
        return frozenset({self.name})

    @property
    def doubts(self) -> FrozenSet[Doubt]:
        return frozenset({Doubt.TREE})

    @property
    def found_at(self) -> Tuple[Path, ...]:
        return (self.location,)

    def describe(self, root: Path) -> str:
        importing = (
            "" if self.site is None else f" ({self.site.where(root)}: {self.site.statement()})"
        )
        return (
            f"{self.name} is imported as a top-level name, and its only location"
            f" {_shown(self.location, root)} is inside {_shown(self.package, root)},"
            f" which the program also imports as a package{importing}"
        )


@dataclass(frozen=True)
class RelativeImportEscapes:
    """A relative import that climbs above the top-level package of its file.

    Python refuses it ("attempted relative import beyond top-level package")
    whenever the statement runs. Where it runs and holds, the file is part of
    a larger package than the tree shows, so the attested name whose location
    holds the file, ``name``, may not be the name the program imports it by:
    that name is in doubt.
    """

    site: ImportSite
    name: Optional[str] = None
    """The attested name whose location holds the file, when one does."""

    @property
    def names_in_doubt(self) -> FrozenSet[str]:
        return frozenset() if self.name is None else frozenset({self.name})

    @property
    def doubts(self) -> FrozenSet[Doubt]:
        return frozenset({Doubt.TREE})

    @property
    def found_at(self) -> Tuple[Path, ...]:
        return (self.site.file,)

    def describe(self, root: Path) -> str:
        return (
            f"{self.site.where(root)}: {self.site.statement()} climbs out of its top-level package"
        )


@dataclass(frozen=True)
class RelativeImportMissing:
    """A relative import that runs, naming a module that does not exist where the climb ends.

    Like an :class:`UnresolvedImport`, it names a module and not a place: a
    package's ``from ._version import __version__`` names a module its build
    generates. It leaves no name in doubt, and the file making it is neither
    a provider nor given a new import.
    """

    site: ImportSite
    missing: str
    package: Optional[str] = None
    """The attested name whose location holds the file, when one does."""

    @property
    def names_in_doubt(self) -> FrozenSet[str]:
        return frozenset()

    @property
    def doubts(self) -> FrozenSet[Doubt]:
        return frozenset()

    @property
    def found_at(self) -> Tuple[Path, ...]:
        return (self.site.file,)

    @property
    def from_inside(self) -> bool:
        """Whether the file lies in a package the program names, whose module it lacks."""
        return self.package is not None

    def describe(self, root: Path) -> str:
        return f"{self.site.where(root)}: {self.site.statement()} names {self.missing}, which does not exist"


ImportProblem = Union[
    AmbiguousName,
    UnresolvedImport,
    FileUnderTwoNames,
    TopLevelInsidePackage,
    RelativeImportEscapes,
    RelativeImportMissing,
]
"""A reason the program's imports do not name its modules unambiguously.

Each kind says which top-level names it leaves in doubt (``names_in_doubt``),
which are never spelled into, what that doubt comes from (``doubts``), and
where in the tree it lies (``found_at``):
the locations it concerns, or the file holding its import, which is never a
provider and never given a new import.
"""

MissingModule = Union[UnresolvedImport, RelativeImportMissing]
"""An import of a module the tree lacks: it leaves no name in doubt, only its own file."""


# -- Spellings ----------------------------------------------------------------


class SpellingBasis(enum.Enum):
    """Why an import was spelled the way it was."""

    RELATIVE = "the modules share a package, and a relative import holds wherever it is imported"
    OWN_PACKAGE = "the importing module spells its own package absolutely and never relatively"
    ATTESTED = "the importing module's context already imports that top-level package"


@dataclass(frozen=True)
class ImportSpelling:
    """How an importer names a provider: ``from <module> import helper``, and why.

    ``evidence`` is the existing import that attests the top-level name an
    absolute spelling starts with; a relative spelling needs none.
    """

    module: str
    basis: SpellingBasis
    evidence: Optional[ImportSite] = None

    def describe(self, root: Path) -> str:
        if self.evidence is None:
            return f"{self.module}: {self.basis.value}"
        return (
            f"{self.module}: {self.basis.value}"
            f" ({self.evidence.where(root)}: {self.evidence.statement()})"
        )


# -- The model ----------------------------------------------------------------


@dataclass(frozen=True)
class _Module:
    """A Python file's imports (``None`` when it does not parse), and what it does to imports."""

    sites: Optional[Tuple[ImportSite, ...]]
    changes_sys_path: bool
    registers_modules: bool = False
    """Whether it stores into ``sys.modules`` or adds a finder: its submodules need no files."""
    binds: Optional[FrozenSet[str]] = None
    """The names its module level binds, or ``None`` when it may bind names no statement shows."""


@dataclass(frozen=True)
class _Owner:
    """A trusted top-level name and the location it names."""

    name: str
    location: Path


@dataclass(frozen=True)
class ImportModel:
    """The program's module names, the problems its imports have, and how to spell new ones.

    Build it with :func:`build_import_model`. Every query is answered from the
    tree as it was read then; Towel adds imports and functions to existing
    files but never adds or removes one, and every import it adds is one this
    model spelled, so the answers stay true for the run.
    """

    root: Path
    names: Mapping[str, TopLevelName]
    problems: Tuple[ImportProblem, ...]
    _modules: Mapping[Path, _Module]
    _packages: FrozenSet[Path]
    _directories: FrozenSet[Path]
    _package_of: Mapping[Path, Optional[Path]]
    _contexts: Mapping[Path, Path]
    _attestations: Mapping[Path, Mapping[str, ImportSite]]
    _trusted: Mapping[Path, str]
    _blocked: FrozenSet[Path]
    _flagged_files: FrozenSet[Path]
    _entered: Mapping[Path, FrozenSet[Path]]
    """For each directory, the contexts with a module outside it that loads a module in it."""
    _left_out: Mapping[Path, FrozenSet[Artifact]]
    """For each module a declared build exclusion covers, what it is left out of."""

    def spelling(self, importer: Path, provider: Path) -> Optional[ImportSpelling]:
        """The module ``importer`` can name ``provider`` by, or ``None`` when none is known to work.

        ``None`` is the answer whenever the program's own imports do not show
        the spelling works: the caller declines rather than guess. A file
        holding a problem's import is given no new import, since how it runs
        is what the tree does not show.
        """
        importing, providing = importer.resolve(), provider.resolve()
        source, target = self._modules.get(importing), self._modules.get(providing)
        if importing == providing or source is None or target is None:
            return None
        if providing.name == "__main__.py":
            # A package's __main__ runs as the program, under the name __main__;
            # importing it by any other name runs the program a second time.
            return None
        if source.sites is None or target.sites is None or importing in self._flagged_files:
            return None
        if self._blocked_provider(providing, importing):
            return None
        if not self._only_entered_directories(importing, providing):
            return None
        if not self._ships_wherever(importing, providing):
            return None
        package = self._package_of[importing]
        if package is not None and package == self._package_of[providing]:
            return self._within_package(importing, source.sites, providing, package)
        owner = self._owner(providing)
        if owner is None:
            return None
        evidence = self._attestations.get(self._contexts[importing], {}).get(owner.name)
        module = _absolute_module(owner, providing, self._packages, self._modules)
        if evidence is None or module is None:
            return None
        return ImportSpelling(module, SpellingBasis.ATTESTED, evidence)

    @property
    def importers_of_missing_modules(self) -> FrozenSet[Path]:
        """Every file making an import of a module the tree lacks (:data:`MissingModule`)."""
        return frozenset(
            problem.site.file
            for problem in self.problems
            if isinstance(problem, (UnresolvedImport, RelativeImportMissing))
        )

    def context_of(self, module: Path) -> Optional[Path]:
        """The top-level package ``module`` belongs to, or ``module`` itself when it is in none."""
        return self._contexts.get(module.resolve())

    def attested_by(self, context: Path) -> FrozenSet[str]:
        """The trusted top-level names the runtime imports of ``context`` use."""
        return frozenset(self._attestations.get(context.resolve(), {}))

    def attestation(self, context: Path, name: str) -> Optional[ImportSite]:
        """The first import in ``context`` that attests ``name``, if one does."""
        return self._attestations.get(context.resolve(), {}).get(name)

    def imports_of(self, module: Path) -> Tuple[ImportSite, ...]:
        """Every import ``module`` contains, in source order; empty when it does not parse."""
        known = self._modules.get(module.resolve())
        return known.sites or () if known is not None else ()

    def module_name(self, module: Path) -> Optional[str]:
        """The absolute name the program's imports give ``module``, when a trusted name reaches it."""
        path = module.resolve()
        owner = self._owner(path)
        if owner is None or path not in self._modules or self._blocked_provider(path):
            return None
        return _absolute_module(owner, path, self._packages, self._modules)

    def split_qualified(self, dotted: str) -> Optional[Tuple[Path, str, str]]:
        """The file, module name and qualified name within it that ``dotted`` denotes.

        A checker names a type by its whole path, ``pkg.mod.Outer.Inner``, and
        the longest prefix naming a module file of a trusted name is the
        module. ``None`` when no such prefix exists.
        """
        parts = dotted.split(".")
        info = self.names.get(parts[0])
        if len(parts) < 2 or info is None or not info.trusted or info.location is None:
            return None
        for end in range(len(parts) - 1, 0, -1):
            found = self._module_file(info.location, parts[1:end])
            if found is not None and not self._blocked_provider(found):
                return found, ".".join(parts[:end]), ".".join(parts[end:])
        return None

    def files_reached(
        self, importer: Path, level: int, module: Optional[str], names: Sequence[str] = ()
    ) -> FrozenSet[Path]:
        """The project files an import in ``importer`` may execute, package initializers included.

        An ambiguous name reaches every candidate, since any of them may be
        the one that runs; an external name reaches no project file.
        """
        reached = _reached(
            self._modules,
            self._directories,
            self.names,
            self.root,
            importer.resolve(),
            level,
            module,
            names,
        )
        return frozenset(reached)

    # -- helpers --------------------------------------------------------------

    def _within_package(
        self,
        importer: Path,
        sites: Tuple[ImportSite, ...],
        provider: Path,
        package: Path,
    ) -> Optional[ImportSpelling]:
        owner = self._owner(package)
        if owner is not None and _spells_package_absolutely(sites, package, owner):
            evidence = self._attestations.get(package, {}).get(owner.name)
            module = _absolute_module(owner, provider, self._packages, self._modules)
            if evidence is not None and module is not None:
                return ImportSpelling(module, SpellingBasis.OWN_PACKAGE, evidence)
        relative = _relative_module(importer, provider, package)
        return None if relative is None else ImportSpelling(relative, SpellingBasis.RELATIVE)

    def _owner(self, path: Path) -> Optional[_Owner]:
        """The innermost trusted location holding ``path``; a module has at most one name."""
        for candidate in (path, *path.parents):
            name = self._trusted.get(candidate)
            if name is not None:
                return _Owner(name, candidate)
            if candidate == self.root:
                break
        return None

    def _only_entered_directories(self, importer: Path, provider: Path) -> bool:
        """Whether every directory the import enters is one its own side already imports from.

        Test code may depend on the library and the library never on its
        tests, and that holds inside a package too: beautifulsoup4 keeps
        ``bs4/tests`` inside ``bs4`` and leaves it out of the wheel, so a
        relative import of it from ``bs4/formatter.py`` resolves in the source
        tree and breaks every installation. So each directory the new import
        enters, from the provider's own up to the first that holds the
        importer, must already be imported from by a module outside it in the
        importer's context or in the provider's own package, with an import
        that runs whenever that module is imported (:attr:`ImportSite.loads`):
        the owner's rule for top-level packages, at every level. An import
        from anywhere else, a documentation script say, shows only that the
        directory exists where that script runs, and one inside a function
        only that it exists where that function is called: shop's
        ``cli.main`` imports ``shop.devtools`` only for a developer's command,
        and the wheel leaves ``shop/devtools`` out.
        """
        sides = {self._contexts[importer], self._contexts[provider]}
        directory = provider.parent
        while not importer.is_relative_to(directory):
            if not sides & self._entered.get(directory, frozenset()):
                return False
            if directory == self.root or directory.parent == directory:
                break
            directory = directory.parent
        return True

    def _ships_wherever(self, importer: Path, provider: Path) -> bool:
        """Whether the provider ships in every artifact of the project that ships the importer.

        A directory that ships can still leave one of its files behind: hatch's
        ``exclude = ["src/shop/_devtools.py"]`` keeps ``_devtools.py`` out of the
        wheel beside a ``shop/stats.py`` that does ship, and a helper hosted in
        it made the installed ``shop.stats`` raise ``ModuleNotFoundError``. What
        the build configuration declares left out is read for that alone
        (:mod:`towel.shipped_files`); it names nothing.
        """
        missing = self._left_out.get(provider, frozenset())
        return not missing or missing <= self._left_out.get(importer, frozenset())

    def _blocked_provider(self, provider: Path, importer: Optional[Path] = None) -> bool:
        """Whether importing ``provider`` could load the wrong file, or a file that cannot load.

        Importing a module through its package runs every initializer above
        it, so an initializer holding a problem's import blocks the modules
        below it, except for an ``importer`` that is itself imported through
        that package: its own import has run the initializer already, and a
        new import of its sibling runs it nowhere it did not run.
        """
        if provider in self._flagged_files:
            return True
        for candidate in (provider, *provider.parents):
            if candidate in self._blocked:
                return True
            if candidate == self.root:
                break
        return any(
            directory / "__init__.py" in self._flagged_files
            and not self._imported_through(importer, directory)
            for directory in _chain(provider.parent, self._packages, self.root)
        )

    def _imported_through(self, module: Optional[Path], directory: Path) -> bool:
        """Whether importing ``module`` runs ``directory``'s initializer: it is below it in its package."""
        if module is None:
            return False
        package = self._package_of.get(module)
        return (
            package is not None
            and module.is_relative_to(directory)
            and directory.is_relative_to(package)
        )

    def _module_file(self, location: Path, inner: Sequence[str]) -> Optional[Path]:
        if location in self._modules:
            return None if inner else location
        cursor = location.joinpath(*inner)
        initializer = cursor / "__init__.py"
        if initializer in self._modules:
            return initializer
        if inner and cursor.with_name(f"{inner[-1]}.py") in self._modules:
            return cursor.with_name(f"{inner[-1]}.py")
        return None


def _reached(
    modules: Mapping[Path, _Module],
    directories: FrozenSet[Path],
    names: Mapping[str, TopLevelName],
    root: Path,
    importer: Path,
    level: int,
    module: Optional[str],
    imported: Sequence[str],
) -> List[Path]:
    """The project files one import may execute: initializers on the way, then the modules named."""
    parts = module.split(".") if module else []
    if level == 0:
        info = names.get(parts[0]) if parts else None
        if info is None:
            return []
        return [
            path
            for location in info.candidates
            for path in _along(modules, directories, location, parts[1:], imported)
        ]
    base = _climb(importer.parent, level - 1, root)
    return [] if base is None else _along(modules, directories, base, parts, imported)


def _along(
    modules: Mapping[Path, _Module],
    directories: FrozenSet[Path],
    location: Path,
    parts: Sequence[str],
    imported: Sequence[str],
) -> List[Path]:
    if location in modules:
        return [location]  # A module file: what it "contains" are attributes, if anything.
    reached: List[Path] = []
    cursor = location
    if cursor / "__init__.py" in modules:
        reached.append(cursor / "__init__.py")
    for part in parts:
        cursor = cursor / part
        if cursor / "__init__.py" in modules:
            reached.append(cursor / "__init__.py")
        elif cursor.with_name(f"{part}.py") in modules:
            reached.append(cursor.with_name(f"{part}.py"))
            return reached
        elif cursor not in directories:
            return reached
    for name in imported:
        if cursor / name / "__init__.py" in modules:
            reached.append(cursor / name / "__init__.py")
        elif cursor / f"{name}.py" in modules:
            reached.append(cursor / f"{name}.py")
    return reached


def _entered_from_outside(
    modules: Mapping[Path, _Module],
    directories: FrozenSet[Path],
    names: Mapping[str, TopLevelName],
    contexts: Mapping[Path, Path],
    root: Path,
) -> Dict[Path, FrozenSet[Path]]:
    """For each directory, the contexts that load a module in it from outside it.

    Only an import that runs whenever its module is imported counts
    (:attr:`ImportSite.loads`).
    """
    entered: Dict[Path, Set[Path]] = {}
    for path, module in modules.items():
        for site in module.sites or ():
            if not site.loads:
                continue
            reached = _reached(
                modules, directories, names, root, path, site.level, site.module, site.names
            )
            for target in reached:
                directory = target.parent
                while not path.is_relative_to(directory):
                    entered.setdefault(directory, set()).add(contexts[path])
                    if directory == root or directory.parent == directory:
                        break
                    directory = directory.parent
    return {directory: frozenset(sides) for directory, sides in entered.items()}


def _spells_package_absolutely(sites: Tuple[ImportSite, ...], package: Path, owner: _Owner) -> bool:
    """Whether a file imports its own package absolutely, and nothing relatively.

    Its absolute imports hold wherever it runs, including as a script with
    its package installed, where a relative import would fail; so its own
    style is kept. A file using both styles, or neither, gets the relative
    import, which holds wherever its package is imported as one.
    """
    if any(site.level for site in sites):
        return False
    inside = package.relative_to(owner.location).parts
    for site in sites:
        if site.top_level != owner.name or site.module is None:
            continue
        inner = site.module.split(".")[1:]
        paths = [inner, *([*inner, name] for name in site.names)]
        if any(tuple(path[: len(inside)]) == inside for path in paths):
            return True
    return False


def _relative_module(importer: Path, provider: Path, package: Path) -> Optional[str]:
    """``provider`` relative to ``importer``, climbing no higher than ``package``.

    One dot names the importer's own package; each further dot one package up.
    """
    directory = importer.parent
    dots = 1
    while not provider.is_relative_to(directory):
        if directory == package:
            return None
        directory = directory.parent
        dots += 1
    parts = _module_parts(provider.relative_to(directory))
    if not all(_is_identifier(part) for part in parts):
        return None
    return "." * dots + ".".join(parts)


def _absolute_module(
    owner: _Owner, provider: Path, packages: FrozenSet[Path], modules: Mapping[Path, _Module]
) -> Optional[str]:
    """``provider``'s name under ``owner``, when every build backend would ship it there.

    Below a regular package every directory must be one too: a directory
    without ``__init__.py`` inside a package imports in the source tree, but
    setuptools' ``find_packages`` leaves it out of the wheel. Namespace
    directories are accepted only above the regular packages, the PEP 420
    shape every backend's namespace support ships.
    """
    location = owner.location
    if location in modules:
        return owner.name if provider == location else None
    if not provider.is_relative_to(location):
        return None
    namespace_seen = False
    for directory in _directories_between(provider.parent, location):
        if directory in packages:
            if namespace_seen:
                return None
        else:
            namespace_seen = True
    parts = _module_parts(provider.relative_to(location))
    if not all(_is_identifier(part) for part in parts):
        return None
    return ".".join([owner.name, *parts])


def _directories_between(directory: Path, top: Path) -> Iterator[Path]:
    """``directory`` and each parent up to and including ``top``."""
    while True:
        yield directory
        if directory == top or directory.parent == directory:
            return
        directory = directory.parent


def _module_parts(relative: Path) -> List[str]:
    """The dotted components a path relative to a package names: no suffix, no ``__init__``."""
    parts = (
        list(relative.with_suffix("").parts) if relative.suffix == ".py" else list(relative.parts)
    )
    if parts and parts[-1] == "__init__":
        parts.pop()
    return parts


def _is_identifier(part: str) -> bool:
    return part.isidentifier() and not iskeyword(part)


def _climb(directory: Path, steps: int, root: Path) -> Optional[Path]:
    """``directory`` after ``steps`` parents, or ``None`` when that leaves ``root``."""
    for _ in range(steps):
        if directory == root:
            return None
        directory = directory.parent
    return directory


def _chain(directory: Path, packages: FrozenSet[Path], root: Path) -> Tuple[Path, ...]:
    """The regular packages enclosing a file in ``directory``, innermost first, within ``root``."""
    chain: List[Path] = []
    while directory in packages:
        chain.append(directory)
        if directory == root:
            break
        directory = directory.parent
    return tuple(chain)


def _shown(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) and path != root else str(path)


# -- Reading the tree ---------------------------------------------------------

_WALK_SKIPPED = SKIPPED_DIRECTORIES - {"build", "dist"}
"""What the consumer scan skips, except build outputs.

A stale ``build/lib/alpha`` is exactly the second copy that makes ``alpha``
ambiguous, so it has to be seen to be reported. The rest hold no source, or
are environments the project is installed into, which the interpreter probe
speaks for.
"""

_INSTALLATIONS = frozenset({"site-packages", "dist-packages"})
_ENVIRONMENT_MARKERS = ("pyvenv.cfg", "conda-meta")
_NOT_PROJECT_MODULES = frozenset({"__future__", "__main__"})


@dataclass(frozen=True)
class _Tree:
    """The project's Python files and the directories that structure them."""

    root: Path
    modules: Tuple[Path, ...]
    packages: FrozenSet[Path]
    """Directories holding an ``__init__.py``."""
    holding_modules: FrozenSet[Path]
    """Directories with a Python file somewhere below them."""
    links: Tuple[Path, ...]
    """Symbolic links to directories or ``.py`` files, which are not followed."""


def _raise(error: OSError) -> None:
    """An unreadable directory may hold a second copy of a package; never model part of a tree."""
    raise error


def _walk(root: Path, excluded: FrozenSet[Path], excluded_names: FrozenSet[str]) -> _Tree:
    modules: List[Path] = []
    packages: Set[Path] = set()
    links: List[Path] = []
    for parent, directories, files in os.walk(root, followlinks=False, onerror=_raise):
        here = Path(parent)
        kept: List[str] = []
        for name in directories:
            path = here / name
            if (
                name in _WALK_SKIPPED
                or name in _INSTALLATIONS
                or name in excluded_names
                or name.startswith(".")
                or path in excluded
            ):
                continue
            if path.is_symlink():
                links.append(path)
            elif not any((path / marker).exists() for marker in _ENVIRONMENT_MARKERS):
                kept.append(name)
        directories[:] = sorted(kept)
        if "__init__.py" in files:
            packages.add(here)
        for name in sorted(files):
            path = here / name
            if not name.endswith(".py") or path in excluded:
                continue
            if path.is_symlink():
                links.append(path)
                continue
            modules.append(path)
            if len(modules) > MAXIMUM_FILES:
                raise ScanLimitExceeded(
                    f"{root} holds more than {MAXIMUM_FILES} Python files, so the names"
                    " its imports give its modules cannot be established"
                )
    holding: Set[Path] = set()
    for module in modules:
        directory = module.parent
        while directory not in holding:
            holding.add(directory)
            if directory == root:
                break
            directory = directory.parent
    return _Tree(root, tuple(modules), frozenset(packages), frozenset(holding), tuple(links))


@dataclass(frozen=True)
class _Submodules:
    """What an import names below its top-level location: found, missing, or neither."""

    resolved: bool
    """It names a module the location holds, which ties the name to the location."""
    missing: Optional[str] = None
    """The first module it needs that the location does not hold, dotted."""


class _Listings:
    """Directory contents read once per build, for the existence questions validation asks.

    The same few package directories are asked about for every import of
    them, thousands of times in a large project.
    """

    def __init__(self, modules: Mapping[Path, _Module]) -> None:
        self._modules = modules
        self._read: Dict[Path, Tuple[FrozenSet[str], FrozenSet[str]]] = {}

    def entries(self, directory: Path) -> Tuple[FrozenSet[str], FrozenSet[str]]:
        """The names of ``directory``'s subdirectories and of its files."""
        known = self._read.get(directory)
        if known is None:
            subdirectories: Set[str] = set()
            files: Set[str] = set()
            try:
                with os.scandir(directory) as scan:
                    for entry in scan:
                        (subdirectories if entry.is_dir() else files).add(entry.name)
            except OSError:
                pass  # Not a directory, or gone: it holds nothing an import could name.
            known = self._read[directory] = (frozenset(subdirectories), frozenset(files))
        return known

    def holds_module(self, directory: Path, name: str) -> bool:
        """Whether ``directory`` holds something an import of ``name`` could load.

        A stub or a compiled extension counts: ``from ._speedups import f``
        names a module a build produces, and calling that import broken would
        be a problem report the program does not have.
        """
        subdirectories, files = self.entries(directory)
        if name in subdirectories or {f"{name}.py", f"{name}.pyi", f"{name}.pyx"} & files:
            return True
        prefix = f"{name}."
        return any(file.startswith(prefix) and file.endswith(_EXTENSIONS) for file in files)

    def first_missing(self, directory: Path, parts: Sequence[str]) -> Optional[int]:
        """The index of the first of ``parts`` that ``directory`` does not hold, or ``None``.

        A module that is not a package holds no submodules, so below one the
        part after it is the one missing: with ``util.py`` present,
        ``util.gone`` lacks ``gone``, not ``util``.
        """
        cursor = directory
        for index, part in enumerate(parts):
            if index == len(parts) - 1:
                return None if self.holds_module(cursor, part) else index
            if part not in self.entries(cursor)[0]:
                return index + 1 if self.holds_module(cursor, part) else index
            cursor = cursor / part
        return None

    def submodules(self, location: Path, site: ImportSite) -> _Submodules:
        """What the absolute import ``site`` names below its top-level name's ``location``.

        ``from Q.x import y`` needs ``Q.x``, and ``y`` too when ``Q.x`` is a
        package whose initializer does not bind it (:meth:`unbound`);
        ``from Q import x`` ties ``Q`` to the location when ``x`` is a module
        there, and needs ``Q.x`` when nothing there binds ``x``. A module is
        not missing from a package whose initializer registers modules itself.
        """
        parts = (site.module or "").split(".")
        inner = parts[1:]
        if location in self._modules:
            if not inner or self._registers(location):
                return _Submodules(False)
            return _Submodules(False, ".".join(parts[:2]))  # A module file has no submodules.
        if not inner:
            held = any(name != "*" and self.holds_module(location, name) for name in site.names)
            unbound = self.unbound(location, site.names)
            return _Submodules(held, None if unbound is None else f"{parts[0]}.{unbound}")
        index = self.first_missing(location, inner)
        if index is None:
            unbound = self.unbound(location.joinpath(*inner), site.names)
            return _Submodules(True, None if unbound is None else f"{site.module}.{unbound}")
        if self._registers(self._above(location, inner[:index])):
            return _Submodules(False)
        return _Submodules(False, ".".join(parts[: index + 2]))

    def unbound(self, directory: Path, names: Sequence[str]) -> Optional[str]:
        """The first of ``names`` a package ``directory`` neither holds as a module nor binds, if any.

        ``from . import generated_at_build`` names a submodule or an attribute
        of the package, and a package's attributes are what its initializer
        binds: where it binds no such name and no such module exists, the
        import fails as ``from .generated_at_build import VERSION`` does. A
        namespace directory binds nothing but its modules. An initializer
        that may bind names no statement shows (a star import, a module
        ``__getattr__``, ``globals()``, a change to ``__path__`` or to
        ``sys.modules``), or that is not Python source, leaves every name
        possible. ``directory`` not being a directory leaves every name so too.
        """
        subdirectories, files = self.entries(directory)
        if not subdirectories and not files:
            return None
        initializer = self._modules.get(directory / "__init__.py")
        if initializer is None:
            if any(file.startswith("__init__.") for file in files):
                return None
            binds: FrozenSet[str] = frozenset()
        elif initializer.binds is None or initializer.registers_modules:
            return None
        else:
            binds = initializer.binds
        return next(
            (
                name
                for name in names
                if name != "*"
                and name not in binds
                and name not in _IMPLICIT_MODULE_NAMES
                and not self.holds_module(directory, name)
            ),
            None,
        )

    def _above(self, location: Path, inner: Sequence[str]) -> Path:
        """The module a missing submodule of ``inner`` would be registered by, below ``location``.

        That is the package's initializer, or the module file itself when
        ``inner`` names one, as ``six.py`` makes ``six.moves``.
        """
        parent = location.joinpath(*inner)
        module = parent.with_name(f"{parent.name}.py")
        return module if inner and module in self._modules else parent / "__init__.py"

    def _registers(self, path: Path) -> bool:
        module = self._modules.get(path)
        return module is not None and module.registers_modules


_EXTENSIONS = tuple(sorted(set(importlib.machinery.EXTENSION_SUFFIXES) | {".so", ".pyd"}))

_IMPLICIT_MODULE_NAMES = frozenset(
    {
        "__name__",
        "__file__",
        "__path__",
        "__doc__",
        "__spec__",
        "__loader__",
        "__package__",
        "__builtins__",
        "__cached__",
        "__dict__",
    }
)
"""What every module or package has without binding it."""


def _read_module(path: Path) -> _Module:
    try:
        with warnings.catch_warnings():
            # Test data is full of invalid escapes; reading it is not the place to say so.
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError, RecursionError):
        # It cannot run, so it imports nothing and nothing can import it.
        return _Module(None, False)
    return _scan(tree, path)


@dataclass(frozen=True)
class _When:
    runtime: bool = True
    guarded: bool = False
    deferred: bool = False


def _scan(tree: ast.Module, path: Path) -> _Module:
    """Every import in ``tree``, module level or not, and what the file does to ``sys``.

    Walked with an explicit stack: a deeply nested file must not exhaust the
    interpreter's recursion limit here when it parsed.
    """
    sites: List[ImportSite] = []
    changes_sys_path = registers_modules = False
    pending: List[Tuple[ast.AST, _When]] = [(tree, _When())]
    while pending:
        node, when = pending.pop()
        if isinstance(node, ast.Import):
            sites.extend(
                ImportSite(
                    path, node.lineno, 0, alias.name, (), when.runtime, when.guarded, when.deferred
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            sites.append(
                ImportSite(
                    path,
                    node.lineno,
                    node.level,
                    node.module,
                    names,
                    when.runtime,
                    when.guarded,
                    when.deferred,
                )
            )
        else:
            changes_sys_path = changes_sys_path or _changes_list(node, "path")
            registers_modules = registers_modules or _registers_modules(node)
            pending.extend(_children(node, when))
    ordered = sorted(sites, key=lambda site: (site.line, site.level, site.module or ""))
    return _Module(tuple(ordered), changes_sys_path, registers_modules, _module_bindings(tree))


_OPAQUE_NAMESPACE_CALLS = frozenset({"globals", "vars", "locals", "setattr", "exec", "eval"})
"""Names through which a module can bind names no statement shows, from anywhere in it."""


def _module_bindings(tree: ast.Module) -> Optional[FrozenSet[str]]:
    """The names ``tree``'s module level binds, or ``None`` when it may bind names no statement shows.

    Every statement that runs at module level is read, inside ``if``, ``try``,
    loops and ``with`` too, but no function or class body. An over-count only
    lets an import pass unreported, so every store counts, guarded or not. A
    ``from . import x`` binds no attribute of its own: it names the
    submodule, which either exists or fails to import; ``as y`` binds ``y``.
    A module-level ``__getattr__``, a star import, a change to ``__path__``,
    or any use anywhere in the file of ``globals``, ``vars``, ``locals``,
    ``setattr``, ``exec`` or ``eval``, as bs4's ``register_treebuilders_from``
    uses ``setattr`` on its own module, leaves every name possible.
    """
    if any(
        (isinstance(node, ast.Name) and node.id in _OPAQUE_NAMESPACE_CALLS)
        or (isinstance(node, ast.Attribute) and node.attr == "__dict__")
        for node in ast.walk(tree)
    ):
        return None
    bound: Set[str] = set()
    pending: List[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "__getattr__" and not isinstance(node, ast.ClassDef):
                return None
            bound.add(node.name)
            pending.extend(node.decorator_list)
            continue
        if isinstance(
            node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            continue
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return None
            submodules = node.level and node.module is None
            bound.update(
                alias.asname or alias.name
                for alias in node.names
                if not submodules or alias.asname not in (None, alias.name)
            )
            continue
        if isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.partition(".")[0] for alias in node.names)
            continue
        if isinstance(node, ast.Name):
            if node.id == "__path__" and not isinstance(node.ctx, ast.Load):
                return None
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif node.id == "__path__":
                return None
            continue
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            bound.add(node.rest)
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(bound)


def _children(node: ast.AST, when: _When) -> Iterator[Tuple[ast.AST, _When]]:
    """``node``'s children, each with when it runs relative to the module's import."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        deferred = replace(when, deferred=True)
        yield from ((child, deferred) for child in ast.iter_child_nodes(node))
        return
    if isinstance(node, ast.If):
        positive = _type_checking(node.test)
        if positive is not None:
            checker = replace(when, runtime=False)
            yield node.test, when
            yield from ((statement, checker if positive else when) for statement in node.body)
            yield from ((statement, when if positive else checker) for statement in node.orelse)
            return
    if isinstance(node, (ast.Try, ast.TryStar)):
        tried = replace(when, guarded=True) if _handles_import_errors(node.handlers) else when
        yield from ((statement, tried) for statement in node.body)
        yield from ((handler, when) for handler in node.handlers)
        yield from ((statement, when) for statement in [*node.orelse, *node.finalbody])
        return
    if isinstance(node, (ast.With, ast.AsyncWith)) and any(
        _suppresses_import_errors(item.context_expr) for item in node.items
    ):
        yield from ((item, when) for item in node.items)
        yield from ((statement, replace(when, guarded=True)) for statement in node.body)
        return
    yield from ((child, when) for child in ast.iter_child_nodes(node))


def _type_checking(test: ast.expr) -> Optional[bool]:
    """True for ``TYPE_CHECKING``, False for ``not TYPE_CHECKING``, None for any other test.

    ``TYPE_CHECKING and anything`` is true only for a checker, so its body
    never runs either; ``or`` can run.
    """
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return True if any(_type_checking(value) is True for value in test.values) else None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _type_checking(test.operand)
        return None if inner is None else not inner
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return None


_IMPORT_ERROR_CATCHERS = frozenset(
    {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
)


def _catches_import_errors(expression: ast.expr) -> bool:
    if isinstance(expression, ast.Tuple):
        return any(_catches_import_errors(element) for element in expression.elts)
    if isinstance(expression, ast.Name):
        return expression.id in _IMPORT_ERROR_CATCHERS
    if isinstance(expression, ast.Attribute):
        return expression.attr in _IMPORT_ERROR_CATCHERS
    return False


def _handles_import_errors(handlers: Sequence[ast.ExceptHandler]) -> bool:
    return any(handler.type is None or _catches_import_errors(handler.type) for handler in handlers)


def _suppresses_import_errors(expression: ast.expr) -> bool:
    """``suppress(ImportError)`` or ``contextlib.suppress(...)`` naming an import error."""
    if not isinstance(expression, ast.Call):
        return False
    function = expression.func
    name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)
    return name == "suppress" and any(_catches_import_errors(arg) for arg in expression.args)


_MUTATORS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "reverse",
        "sort",
        "update",
        "setdefault",
        "__setitem__",
    }
)


def _is_sys(node: ast.AST, attribute: str) -> bool:
    """Whether ``node`` is ``sys.<attribute>``."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _stored_into(node: ast.AST) -> List[ast.expr]:
    if isinstance(node, (ast.Assign, ast.Delete)):
        return list(node.targets)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


def _changes_list(node: ast.AST, attribute: str) -> bool:
    """Whether ``node`` changes ``sys.<attribute>``: a mutating method, or a store into it."""
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in _MUTATORS:
            return _is_sys(function.value, attribute)
        # site.addsitedir extends sys.path as a side effect.
        name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)
        return attribute == "path" and name == "addsitedir"
    return any(
        _is_sys(target, attribute)
        or (isinstance(target, ast.Subscript) and _is_sys(target.value, attribute))
        for target in _stored_into(node)
    )


_ADDERS = frozenset({"append", "extend", "insert", "update", "setdefault", "__setitem__"})


def _adds_to(node: ast.AST, attribute: str) -> bool:
    """Whether ``node`` adds an entry to ``sys.<attribute>``; removing one adds nothing."""
    if isinstance(node, ast.Call):
        function = node.func
        return (
            isinstance(function, ast.Attribute)
            and function.attr in _ADDERS
            and _is_sys(function.value, attribute)
        )
    if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        return False
    return any(
        _is_sys(target, attribute)
        or (isinstance(target, ast.Subscript) and _is_sys(target.value, attribute))
        for target in _stored_into(node)
    )


def _registers_modules(node: ast.AST) -> bool:
    """Whether ``node`` makes modules no file holds: a store into ``sys.modules``, or a new finder.

    ``six`` builds ``six.moves`` this way and pytest's ``py`` shim ``py.path``;
    an import of either names a module that exists without a file, so its
    absence from the tree says nothing about where the name lives.
    """
    return _adds_to(node, "modules") or _adds_to(node, "meta_path")


# -- Building the model -------------------------------------------------------


def build_import_model(
    root: Path,
    *,
    excluded: Iterable[Path] = (),
    excluded_names: Iterable[str] = (),
    installed: Optional[InstalledProbe] = None,
    required: Optional[Iterable[Requirement]] = None,
    left_out_of: Optional[Mapping[Path, FrozenSet[Artifact]]] = None,
    own: Optional[Iterable[str]] = None,
) -> ImportModel:
    """The import model of every Python file under ``root``, outside ``excluded``.

    ``excluded_names`` leaves out every directory of those names, as the
    CLI's ``--exclude`` does, which is how a stray copy is set aside.
    ``installed`` says what outside the project provides a top-level name;
    it defaults to :func:`installed_outside`, this interpreter's own answer.
    ``required`` are the distributions the project declares it requires; they
    default to what ``root``'s metadata, lockfiles and requirements files
    declare (:func:`~towel.declared_requirements.declared_requirements`).
    ``left_out_of`` says which modules the build configuration leaves out of
    what ships; it defaults to what ``root``'s configuration declares
    (:func:`~towel.shipped_files.left_out`). ``own`` are the names the
    project's metadata gives the project itself; they default to
    :func:`~towel.declared_requirements.own_names` of ``root``.
    Raises :class:`~towel.consumers.ScanLimitExceeded` for a tree too large
    to read, since a partial model could miss the copy that makes a name
    ambiguous.
    """
    project = root.resolve()
    if not project.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    probe = installed_outside if installed is None else installed
    tree = _walk(project, frozenset(path.resolve() for path in excluded), frozenset(excluded_names))
    modules = {path: _read_module(path) for path in tree.modules}
    sites = tuple(site for module in modules.values() for site in module.sites or ())
    absolute: Dict[str, List[ImportSite]] = {}
    for site in sites:
        top = site.top_level
        if top is not None and top not in _NOT_PROJECT_MODULES:
            absolute.setdefault(top, []).append(site)
    # Only an import that attests may locate a name or put one in doubt.
    placing = {
        name: [site for site in found if _places(site, modules)] for name, found in absolute.items()
    }
    placed = frozenset(name for name, found in placing.items() if found)
    listings = _Listings(modules)
    entries = _top_level_entries(tree)
    strict = {name: entries.get(name, ()) for name in absolute}
    used_strictly = _used_packages(
        tree, [site for site in sites if site.level or _places(site, modules)], strict, listings
    )
    relaxed, inside_used = _relaxed_candidates(tree, used_strictly, placing, strict, listings)
    found = {name: strict[name] or relaxed.get(name, ()) for name in absolute}
    classified = {
        name: _classify(name, found[name], placing[name], tree, listings, probe, project)
        for name in absolute
    }
    requirements = tuple(declared_requirements(project) if required is None else required)
    classified, modules_inside, scripts = _modules_beside_their_importers(
        classified, placing, tree, used_strictly, requirements, lambda name: probe(name, project)
    )
    inside_used = {**inside_used, **modules_inside}
    names, lacking = _checked(dict(sorted(classified.items())), absolute, listings)
    names, lacking = _required_elsewhere(names, lacking, requirements)
    project_names = own_names(project) if own is None else frozenset(map(normalized_name, own))
    names, lacking = _lacking_elsewhere(names, lacking, project_names, modules)
    unresolved = [
        problem for problems in lacking.values() for problem in problems if problem.site.runtime
    ]
    located = {
        name: info.candidates
        for name, info in names.items()
        if info.status is not NameStatus.EXTERNAL
    }
    used = _used_packages(tree, sites, located)
    # Every place a name was looked for bounds the packages above it, whatever
    # the name turned out to be: the bound only ever withholds a relative import.
    bounds = frozenset(location for locations in found.values() for location in locations)
    package_of = {path: _package_of(path, tree, used, bounds) for path in tree.modules}
    problems = _problems(tree, modules, names, placed, unresolved, inside_used, listings)
    flagged_names, flagged_files = _flags(problems)
    flagged_files |= scripts
    names = {name: replace(info, flagged=name in flagged_names) for name, info in names.items()}
    trusted = {info.candidates[0]: name for name, info in names.items() if info.trusted}
    blocked = frozenset(
        location
        for name, info in names.items()
        if (info.status is NameStatus.AMBIGUOUS and name in placed) or info.flagged
        for location in info.candidates
    )
    contexts = {path: package_of[path] or path for path in tree.modules}
    entered = _entered_from_outside(modules, tree.holding_modules, names, contexts, project)
    left = left_out(project, tree.modules) if left_out_of is None else left_out_of
    return ImportModel(
        root=project,
        names=names,
        problems=tuple(problems),
        _modules=modules,
        _packages=tree.packages,
        _directories=tree.holding_modules,
        _package_of=package_of,
        _contexts=contexts,
        _attestations=_attestations(modules, contexts, frozenset(trusted.values())),
        _trusted=trusted,
        _blocked=blocked,
        _flagged_files=flagged_files,
        _entered=entered,
        _left_out=left,
    )


def _top_level_entries(tree: _Tree) -> Dict[str, Tuple[Path, ...]]:
    """Every directory holding modules and every module not inside a regular package, by name."""
    entries: Dict[str, Set[Path]] = {}
    for directory in tree.holding_modules:
        if directory != tree.root and directory.parent not in tree.packages:
            entries.setdefault(directory.name, set()).add(directory)
    for module in tree.modules:
        if module.parent not in tree.packages:
            entries.setdefault(module.stem, set()).add(module)
    for link in tree.links:
        # A link is not followed, so what it holds is unknown; it is counted
        # by where it leads, which also stops a link to a location already
        # counted from counting it twice.
        if link.parent in tree.packages:
            continue
        try:
            target = link.resolve(strict=True)
        except (OSError, RuntimeError):
            continue  # Dangling: it names nothing.
        name = link.name if target.is_dir() else link.stem
        entries.setdefault(name, set()).add(target)
    return {name: tuple(sorted(paths)) for name, paths in entries.items()}


def _used_packages(
    tree: _Tree,
    sites: Iterable[ImportSite],
    candidates: Mapping[str, Sequence[Path]],
    listings: Optional[_Listings] = None,
) -> Set[Path]:
    """The regular packages the program's imports use as packages.

    A relative import uses its file's package and every package it climbs
    into; an absolute import uses every package its module path passes
    through at any candidate location of its top-level name. Given
    ``listings``, an import naming a module the tree lacks there uses none:
    it fails wherever it runs, so it cannot show that a stray
    ``__init__.py`` makes a package. Test data importing a gone
    ``src.alpha_old`` once put ``alpha`` in doubt that way, and refused the
    run (docs/DECISIONS.md, "An import problem refuses only when it leaves a
    name in doubt").
    """
    used: Set[Path] = set()
    for site in sites:
        if site.level:
            base = _climb(site.file.parent, site.level - 1, tree.root)
            if (
                listings is not None
                and base is not None
                and _missing_below(base, site, listings) is not None
            ):
                continue
            directory = site.file.parent
            for _ in range(site.level):
                if directory not in tree.packages:
                    break
                used.add(directory)
                if directory == tree.root:
                    break
                directory = directory.parent
        elif site.module is not None:
            parts = site.module.split(".")
            for location in candidates.get(parts[0], ()):
                if listings is None or listings.submodules(location, site).missing is None:
                    used.update(_packages_along(tree, location, parts[1:], site.names))
    return used


def _packages_along(
    tree: _Tree, location: Path, parts: Sequence[str], names: Sequence[str]
) -> Iterator[Path]:
    if location not in tree.holding_modules:
        return  # A module file, or a link that is not followed.
    cursor = location
    if cursor in tree.packages:
        yield cursor
    for part in parts:
        cursor = cursor / part
        if cursor in tree.packages:
            yield cursor
        elif cursor not in tree.holding_modules:
            return
    yield from (cursor / name for name in names if cursor / name in tree.packages)


def _relaxed_candidates(
    tree: _Tree,
    used: Set[Path],
    absolute: Mapping[str, Sequence[ImportSite]],
    strict: Mapping[str, Tuple[Path, ...]],
    listings: _Listings,
) -> Tuple[Dict[str, Tuple[Path, ...]], Dict[str, _InsidePackage]]:
    """Locations a stray ``__init__.py`` hid, for the names that have no other candidate.

    A child of a top-level package counts only where some import of its
    name that places it (:func:`_places`) names a module the child holds:
    ``from alpha.a import f`` for ``src/alpha/a.py``. A bare ``import toml``
    beside a package's own ``toml.py`` is no evidence at all, and nor is
    graphene's setup.py importing ``pyutils.version`` inside ``try`` once it
    has put ``graphene`` on ``sys.path``. Under a package the program does use
    as a package, the child is also that package's submodule, and the second
    mapping records the conflict, with the import that names the child.
    """
    tops = sorted(
        directory
        for directory in tree.packages
        if directory == tree.root or directory.parent not in tree.packages
    )
    found: Dict[str, List[Path]] = {}
    inside_used: Dict[str, _InsidePackage] = {}
    for name, sites in absolute.items():
        if strict[name] or not _is_identifier(name):
            continue
        for top in tops:
            child = top / name
            if child not in tree.holding_modules:
                continue
            naming = next(
                (site for site in sites if listings.submodules(child, site).resolved), None
            )
            if naming is not None:
                found.setdefault(name, []).append(child)
                if top in used:
                    inside_used.setdefault(name, _InsidePackage(child, top, naming))
    return {name: tuple(paths) for name, paths in found.items()}, inside_used


def _modules_beside_their_importers(
    names: Mapping[str, TopLevelName],
    absolute: Mapping[str, Sequence[ImportSite]],
    tree: _Tree,
    used: Set[Path],
    required: Sequence[Requirement],
    outside: Callable[[str], Optional[OutsideProvider]],
) -> Tuple[Dict[str, TopLevelName], Dict[str, _InsidePackage], FrozenSet[Path]]:
    """Names nothing else provides that a file in a package imports from a module beside it.

    ``pkg/c.py`` imports ``helpers_top``, and only ``pkg/helpers_top.py`` is
    named so: the import holds only where ``c.py`` runs as a script, with
    ``pkg`` itself on ``sys.path``, so that module is the name's location.
    Where the program also uses the package as one, the second mapping
    records the conflict. Either way the files making such an import, and
    the module they import, the third result, run as top-level modules,
    where a relative import fails: they are never a provider and are given
    no new import. Only an import that
    places its name (:func:`_places`), from another file of the module's
    own directory, is such evidence. A module importing its own name
    (``tqdm/keras.py``'s ``import keras``), or a script elsewhere importing a
    library named like a package's module (mistune's benchmark importing
    ``markdown``), names the library; and so does any import of a name
    something else provides: an installed copy, the standard library, or a
    distribution the project requires.
    """
    requiring = frozenset(requirement.name for requirement in required)
    placed: Dict[str, TopLevelName] = {}
    inside: Dict[str, _InsidePackage] = {}
    scripts: Set[Path] = set()
    modules = frozenset(tree.modules)
    for name, info in names.items():
        if (
            info.status is not NameStatus.EXTERNAL
            or info.installed is not None
            or normalized_name(name) in requiring
            or not _is_identifier(name)
        ):
            continue
        importers: Dict[Path, Dict[Path, ImportSite]] = {}
        for site in absolute[name]:
            sibling = site.file.parent / f"{name}.py"
            if site.attests and sibling in modules and sibling != site.file:
                importers.setdefault(sibling, {}).setdefault(site.file, site)
        if not importers or outside(name) is not None:
            continue
        locations = tuple(sorted(importers))
        status = NameStatus.ATTESTED if len(locations) == 1 else NameStatus.AMBIGUOUS
        placed[name] = TopLevelName(name, status, locations)
        for location in locations:
            scripts.update({location, *importers[location]})
            chain = _chain(location.parent, tree.packages, tree.root)
            package = next((directory for directory in reversed(chain) if directory in used), None)
            if package is not None:
                naming = next(iter(importers[location].values()))
                inside.setdefault(name, _InsidePackage(location, package, naming))
    return {**names, **placed}, inside, frozenset(scripts)


@dataclass(frozen=True)
class _InsidePackage:
    """A top-level name's location inside a package the program uses as one, and the import naming it."""

    location: Path
    package: Path
    site: ImportSite


def _names_a_module_of(sites: Sequence[ImportSite], directory: Path, listings: _Listings) -> bool:
    return any(listings.submodules(directory, site).resolved for site in sites)


def _classify(
    name: str,
    candidates: Tuple[Path, ...],
    sites: Sequence[ImportSite],
    tree: _Tree,
    listings: _Listings,
    installed: InstalledProbe,
    root: Path,
) -> TopLevelName:
    """What ``name`` refers to, given its candidates and what the interpreter finds elsewhere.

    Import takes a built-in or frozen module before searching ``sys.path``,
    and takes a regular package or module found anywhere on it over every
    namespace directory of the name. So an installed regular module leaves
    the project's namespace directories nothing to be: the tests'
    ``testing/logging/`` never displaces the standard ``logging``. A regular
    candidate in the project wins only where its own parent is on the path,
    which the program's imports vouch for only by naming what it holds; a
    namespace directory beside it is dropped unless some import names a
    module that it holds. A mirror ``tests/alpha/`` of test files never
    competes with ``src/alpha``, but chardet's ``scripts/utils.py`` does
    compete with a regular ``tests/data/scripts`` when the tests import
    ``scripts.utils``.
    """
    if not candidates:
        return TopLevelName(name, NameStatus.EXTERNAL, ())
    outside = installed(name, root)
    if outside is not None and outside.kind is ProviderKind.UNSHADOWABLE:
        return TopLevelName(name, NameStatus.EXTERNAL, (), outside.description)
    regular = tuple(
        candidate
        for candidate in candidates
        if candidate in tree.packages or candidate not in tree.holding_modules
    )
    namespaces = tuple(candidate for candidate in candidates if candidate not in regular)
    if regular and outside is not None and outside.kind is ProviderKind.NAMESPACE:
        outside = None
    if outside is not None and outside.kind is ProviderKind.MODULE:
        candidates = regular
    elif regular:
        named = tuple(space for space in namespaces if _names_a_module_of(sites, space, listings))
        candidates = tuple(sorted((*regular, *named)))
    installed_at = None if outside is None else outside.description
    if not candidates:
        return TopLevelName(name, NameStatus.EXTERNAL, (), installed_at)
    if outside is not None or len(candidates) > 1:
        return TopLevelName(name, NameStatus.AMBIGUOUS, candidates, installed_at)
    return TopLevelName(name, NameStatus.ATTESTED, candidates)


def _checked(
    names: Mapping[str, TopLevelName],
    absolute: Mapping[str, Sequence[ImportSite]],
    listings: _Listings,
) -> Tuple[Dict[str, TopLevelName], Dict[str, List[UnresolvedImport]]]:
    """Each attested name checked against its imports, and, by name, the imports its location does not hold.

    A location that holds none of the modules its name's imports need is not
    that name at all: ``examples/celery/`` beside ``from celery.result import
    AsyncResult`` is a namesake of a library this interpreter lacks, and the
    name is external. Where some import does resolve, the location is the
    name, and each one that does not is a problem, unless it never runs: a
    type-only import is not an import edge (docs/DECISIONS.md, "A type-only
    import is not an import edge"), so its file loads wherever it did. What
    a type-only import needs still counts in telling a namesake apart
    (:func:`_lacking_elsewhere`), so every unguarded one is returned.
    """
    checked = dict(names)
    lacking: Dict[str, List[UnresolvedImport]] = {}
    for name, info in names.items():
        location = info.location
        if location is None:
            continue
        resolved = False
        missing: List[UnresolvedImport] = []
        for site in absolute[name]:
            found = listings.submodules(location, site)
            resolved = resolved or found.resolved
            if found.missing is not None and not site.guarded:
                missing.append(UnresolvedImport(site, name, location, found.missing))
        if missing and not resolved:
            checked[name] = TopLevelName(name, NameStatus.EXTERNAL, (), info.installed)
        elif missing:
            lacking[name] = missing
    return checked, lacking


def _required_elsewhere(
    names: Mapping[str, TopLevelName],
    lacking: Mapping[str, List[UnresolvedImport]],
    required: Iterable[Requirement],
) -> Tuple[Dict[str, TopLevelName], Dict[str, List[UnresolvedImport]]]:
    """Each name the tree places that a distribution the project requires also provides, in doubt.

    A project that requires ``click`` imports that distribution under the name
    wherever it is installed, whatever the tree holds: a directory ``click/``
    sharing one module with the library would otherwise be taken for it
    wherever the interpreter running Towel lacks the library, as ``uvx``
    does, and a helper hosted there is imported from a module the library
    lacks. A distribution is matched by its normalized name, so one whose
    import name differs (``PyYAML`` provides ``yaml``) goes unnoticed unless
    this interpreter can import it. An import of such a name that the tree's
    location lacks is then the library's, and no problem of its own.
    """
    first: Dict[str, Requirement] = {}
    for requirement in required:
        first.setdefault(requirement.name, requirement)
    doubted = {
        name: replace(info, status=NameStatus.AMBIGUOUS, required=declared.describe())
        for name, info in names.items()
        if info.status is not NameStatus.EXTERNAL
        and (declared := first.get(normalized_name(name))) is not None
    }
    return (
        {**names, **doubted},
        {name: problems for name, problems in lacking.items() if name not in doubted},
    )


def _lacking_elsewhere(
    names: Mapping[str, TopLevelName],
    lacking: Mapping[str, List[UnresolvedImport]],
    own: FrozenSet[str],
    modules: Mapping[Path, _Module],
) -> Tuple[Dict[str, TopLevelName], Dict[str, List[UnresolvedImport]]]:
    """Each attested name the program imports a module of that its one location lacks, in doubt.

    ``third_party/click/`` holds ``utils.py`` and the program imports both
    ``click.utils`` and ``click.core``: that location is no more the name
    than the library it shares one module with, which the program's own
    ``import click.core`` shows it reaches. Run where the interpreter lacks
    click, Towel took the directory for the name and hosted a helper in
    ``click/utils.py`` that no installation of the program can import
    (round-4 audit P1-3; docs/DECISIONS.md, "An import problem refuses only
    when it leaves a name in doubt": "the fix is to put that name in
    doubt"). An import that lacks is evidence of this only when:

    - it is made from outside the location. The location's own import of a
      module it lacks, a package's ``_version.py`` its build generates,
      names itself, not something else;
    - it may run where the program runs: not guarded, since a guarded import
      expects to fail, and not in a file that changes ``sys.path``, whose
      names mean something else. A type-only import counts: the checker
      reads the program's environment, and it holds the module there;
    - the name is not one the project's metadata gives the project itself.
      A distribution of that name is the project, so what its location
      lacks is missing from the project, as sphinx's test data imports the
      ``sphinx.missing_module4`` its tests mock, and prompt-toolkit's
      examples a module it no longer has.

    Such a name's imports its location lacks are then the other copy's, and
    no problems of their own.
    """
    doubted: Dict[str, TopLevelName] = {}
    for name, problems in lacking.items():
        info = names[name]
        if info.status is not NameStatus.ATTESTED or normalized_name(name) in own:
            continue
        evidence = next(
            (
                problem
                for problem in problems
                if not problem.from_inside and not modules[problem.site.file].changes_sys_path
            ),
            None,
        )
        if evidence is not None:
            doubted[name] = replace(info, status=NameStatus.AMBIGUOUS, lacking=evidence)
    return (
        {**names, **doubted},
        {name: problems for name, problems in lacking.items() if name not in doubted},
    )


def _package_of(
    module: Path, tree: _Tree, used: Set[Path], bounds: FrozenSet[Path]
) -> Optional[Path]:
    """The top-level package ``module`` is imported as part of, or ``None`` outside any.

    That is the outermost enclosing regular package the program uses as a
    package, or the module's own directory when it uses none. It is never
    above a candidate location of a top-level name, since the module may be
    imported by that name, and then nothing above it is a package.
    """
    chain = _chain(module.parent, tree.packages, tree.root)
    if not chain:
        return None
    reach = next((directory for directory in reversed(chain) if directory in used), chain[0])
    bound = next((directory for directory in chain if directory in bounds), None)
    if bound is not None and len(bound.parts) > len(reach.parts):
        return bound
    return reach


def _problems(
    tree: _Tree,
    modules: Mapping[Path, _Module],
    names: Mapping[str, TopLevelName],
    placed: FrozenSet[str],
    unresolved: Sequence[UnresolvedImport],
    inside_used: Mapping[str, _InsidePackage],
    listings: _Listings,
) -> List[ImportProblem]:
    """What the imports get wrong. A name only imports that attest nothing use is in no doubt.

    Such an import, guarded or in a file that changes ``sys.path``, names
    nothing the program relies on, and nothing is spelled into its name
    (:func:`_places`); so it neither locates a name nor makes one ambiguous.
    """
    problems: List[ImportProblem] = [
        AmbiguousName(name, info.candidates, info.installed, info.required, info.lacking)
        for name, info in names.items()
        if info.status is NameStatus.AMBIGUOUS and name in placed
    ]
    problems.extend(
        TopLevelInsidePackage(name, inside.location, inside.package, inside.site)
        for name, inside in sorted(inside_used.items())
        if names[name].status is not NameStatus.EXTERNAL
    )
    problems.extend(unresolved)
    attested = {
        info.candidates[0]: name
        for name, info in names.items()
        if info.status is NameStatus.ATTESTED
    }
    problems.extend(_two_names(attested, tree.root))
    problems.extend(_relative_problems(tree, modules, attested, listings))
    return problems


def _two_names(attested: Mapping[Path, str], root: Path) -> Iterator[FileUnderTwoNames]:
    for inner, inner_name in sorted(attested.items()):
        if not inner.is_relative_to(root):
            continue
        for outer in inner.parents:
            if outer == root:
                break
            outer_name = attested.get(outer)
            if outer_name is None:
                continue
            parts = _module_parts(inner.relative_to(outer))
            if parts and all(_is_identifier(part) for part in parts):
                yield FileUnderTwoNames(inner, (".".join([outer_name, *parts]), inner_name))


def _innermost_location(attested: Mapping[Path, str], path: Path, root: Path) -> Optional[Path]:
    for candidate in (path, *path.parents):
        if candidate in attested:
            return candidate
        if candidate == root:
            break
    return None


def _relative_problems(
    tree: _Tree,
    modules: Mapping[Path, _Module],
    attested: Mapping[Path, str],
    listings: _Listings,
) -> Iterator[ImportProblem]:
    """Relative imports that climb out of their file's name, or name nothing.

    Only files in a regular package are judged. A relative import in a bare
    directory can hold when a runner imports it as a namespace package, and
    one that climbs from a package into a directory without ``__init__.py``
    holds when that directory is a namespace package of the file's name; the
    tree says which only when an attested name reaches the file. A type-only
    import never runs, so a module it names that does not exist is missing
    from nothing that loads; one that climbs out still says the checker sees
    a larger package than the tree shows.
    """
    for path, module in modules.items():
        if module.sites is None or path.parent not in tree.packages:
            continue
        chain = _chain(path.parent, tree.packages, tree.root)
        owner = _innermost_location(attested, path, tree.root)
        name = None if owner is None else attested[owner]
        for site in module.sites:
            if not site.level or site.guarded:
                continue
            base = _climb(path.parent, site.level - 1, tree.root)
            if base is None:
                yield RelativeImportEscapes(site, name)
                continue
            if owner is not None:
                if not base.is_relative_to(owner):
                    yield RelativeImportEscapes(site, name)
                    continue
            elif base not in chain:
                if base == tree.root:
                    yield RelativeImportEscapes(site)
                continue  # A namespace directory no name places: not known to be wrong.
            missing = _missing_below(base, site, listings) if site.runtime else None
            if missing is not None:
                yield RelativeImportMissing(site, "." * site.level + missing, name)


def _missing_below(base: Path, site: ImportSite, listings: _Listings) -> Optional[str]:
    """The first module the relative import ``site`` needs that ``base``, where its climb ends, lacks.

    ``from . import x`` names a submodule or an attribute the package binds;
    ``from .m import x`` needs ``m``, and ``x`` too where ``m`` is a package
    that binds no ``x``. Dotted below ``base``, without the leading dots.
    """
    if site.module is None:
        return listings.unbound(base, site.names)
    parts = site.module.split(".")
    index = listings.first_missing(base, parts)
    if index is not None:
        return ".".join(parts[: index + 1])
    unbound = listings.unbound(base.joinpath(*parts), site.names)
    return None if unbound is None else ".".join([*parts, unbound])


def _flags(problems: Sequence[ImportProblem]) -> Tuple[FrozenSet[str], FrozenSet[Path]]:
    """The names the problems leave in doubt, and the files holding their imports.

    Nothing is spelled into a name in doubt, and a file holding a problem's
    import is never a provider: importing it from somewhere new runs an
    import the tree does not show to work.
    """
    flagged_names = frozenset(name for problem in problems for name in problem.names_in_doubt)
    flagged_files = frozenset(
        problem.site.file
        for problem in problems
        if isinstance(problem, (UnresolvedImport, RelativeImportEscapes, RelativeImportMissing))
    )
    return flagged_names, flagged_files


def _places(site: ImportSite, modules: Mapping[Path, _Module]) -> bool:
    """Whether ``site`` shows where its top-level name lives: it attests, from a file leaving ``sys.path`` alone.

    Only such an import may locate a name, attest it for spelling, or put it
    in doubt (docs/DECISIONS.md, "How the import model decides, and when a
    problem refuses"). graphene's setup.py appends ``graphene`` to
    ``sys.path`` and then, inside ``try``, imports ``pyutils.version``: that
    says nothing about where ``pyutils`` lives for the program, and it once
    refused every ``--cross-module`` run on graphene.
    """
    return site.attests and not modules[site.file].changes_sys_path


def _attestations(
    modules: Mapping[Path, _Module], contexts: Mapping[Path, Path], trusted: FrozenSet[str]
) -> Dict[Path, Dict[str, ImportSite]]:
    """For each context, the first import attesting each trusted name it uses (:func:`_places`)."""
    attestations: Dict[Path, Dict[str, ImportSite]] = {}
    for path, module in modules.items():
        for site in module.sites or ():
            top = site.top_level
            if top in trusted and _places(site, modules):
                attestations.setdefault(contexts[path], {}).setdefault(top, site)
    return attestations


# -- What the interpreter can see ---------------------------------------------


def installed_outside(name: str, root: Path) -> Optional[OutsideProvider]:
    """What this interpreter would import as top-level ``name`` from outside ``root``, if anything.

    This is the search ``importlib.util.find_spec`` makes -- ``sys.modules``,
    then each finder on ``sys.meta_path`` over ``sys.path`` -- with two kinds
    of entry set aside: the project's own, since a copy of the project on the
    path (an editable install) is the project and may hide another copy
    behind it, and the working directory, which is where Towel was started
    and no part of the environment the project runs in. An environment inside
    the root, such as the project's own ``.venv``, is not the project's: what
    is installed there is outside it (:func:`_in_installation`). Only a
    top-level name is asked, so no package's ``__init__`` runs: finders only
    look. A lookup that fails is an answer too, since nothing is then known
    about the name.
    """
    if name in sys.builtin_module_names:
        return OutsideProvider(ProviderKind.UNSHADOWABLE, "a module built into the interpreter")
    loaded = sys.modules.get(name)
    if loaded is not None:
        spec = getattr(loaded, "__spec__", None)
        if spec is None:
            return _lookup_failed(ValueError(f"sys.modules[{name!r}] has no __spec__"))
        provider = _outside(spec, root)
        if provider is not None:
            return provider
    provider = _found_outside(name, root)
    if provider is None and name in sys.stdlib_module_names:
        # A standard module this build lacks (tkinter without Tk) may be in the project's.
        return OutsideProvider(ProviderKind.MODULE, "the standard library")
    return provider


def _lookup_failed(error: Exception) -> OutsideProvider:
    return OutsideProvider(
        ProviderKind.UNKNOWN,
        f"no provider this interpreter could name ({type(error).__name__}: {error})",
    )


def _found_outside(name: str, root: Path) -> Optional[OutsideProvider]:
    working = Path.cwd().resolve()
    entries = [
        entry
        for entry in sys.path
        if not _projects_own(Path(entry or os.curdir).resolve(), root)
        and Path(entry or os.curdir).resolve() != working
    ]
    for finder in sys.meta_path:
        find_spec = getattr(finder, "find_spec", None)
        if find_spec is None:
            continue
        try:
            path = entries if finder is importlib.machinery.PathFinder else None
            spec: Optional[importlib.machinery.ModuleSpec] = find_spec(name, path)
        except Exception as error:  # Any failure leaves the name's provider unknown.
            return _lookup_failed(error)
        provider = None if spec is None else _outside(spec, root)
        if provider is not None:
            return provider
    return None


def _outside(spec: importlib.machinery.ModuleSpec, root: Path) -> Optional[OutsideProvider]:
    """What ``spec`` provides from outside ``root``, or ``None`` when it is the project's own."""
    origin = spec.origin
    if origin in ("built-in", "frozen"):
        return OutsideProvider(ProviderKind.UNSHADOWABLE, f"a {origin} module")
    if origin is not None:
        path = Path(origin).resolve()
        if _projects_own(path, root):
            return None
        return OutsideProvider(ProviderKind.MODULE, str(path))
    portions = [Path(location).resolve() for location in spec.submodule_search_locations or ()]
    outside = [str(portion) for portion in portions if not _projects_own(portion, root)]
    return OutsideProvider(ProviderKind.NAMESPACE, ", ".join(outside)) if outside else None


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _projects_own(path: Path, root: Path) -> bool:
    """Whether ``path`` is part of the project at ``root``: inside it, and not in an environment there."""
    return _within(path, root) and not _in_installation(path, root)


def _in_installation(path: Path, root: Path) -> bool:
    """Whether ``path``, inside ``root``, lies in an environment installed there rather than in its source.

    The project's own ``.venv`` is the usual one, and what is installed in it
    is a distribution, not the project's tree: the project installed there
    without ``-e``, or a library a project directory is named like. It is
    recognized as the tree walk recognizes it, which never reads it: a
    ``site-packages`` or ``dist-packages`` directory, or a directory holding
    ``pyvenv.cfg`` or ``conda-meta`` below the root; and also by this
    interpreter's own installation paths when they lie below the root. The
    root itself is not asked: a project kept in the directory of its own
    environment still has its source there.
    """
    if any(
        _within(path, place)
        for place in _interpreter_places()
        if place != root and _within(place, root)
    ):
        return True
    directory = root
    for part in path.relative_to(root).parts:
        directory = directory / part
        if part in _INSTALLATIONS or any(
            (directory / marker).exists() for marker in _ENVIRONMENT_MARKERS
        ):
            return True
    return False


@functools.lru_cache(maxsize=1)
def _interpreter_places() -> FrozenSet[Path]:
    """This interpreter's prefixes and the directories it installs into, resolved; fixed for a process."""
    places = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
    places.update(
        sysconfig.get_paths().get(key, "") for key in ("stdlib", "platstdlib", "purelib", "platlib")
    )
    return frozenset(Path(place).resolve() for place in places if place)
