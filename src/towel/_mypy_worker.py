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

"""Run mypy in an owned process so its global state cannot change the caller.

The line-delimited protocol contains source text and checker diagnostics only.
The project's configured plugins are loaded as its own mypy loads them (see
``_load_configured_plugins``); configured executables and report destinations
are never used. This file is launched by its absolute installed path with
``python -I -B``, so nothing a plugin imports writes bytecode into the project.

Each request builds in a forked child that exits when it has answered. Nothing
a build allocates outlives it, so the thousandth request costs what the first
did. Successive ``build.build`` calls in one process do not have that property:
mypy 1.19 keeps every rechecked module's tree alive after the result is dropped
(about 250,000 objects per build of ``sphinx.application``, unreachable from any
module and never collected), and each build begins with a full collection that
walks all of it. Requests slowed linearly and a run quadratically: 117 recorded
Sphinx requests took 855 s in one process and 88 s forked, with identical
diagnostics. What persists between requests is the owned cache directory and
this process's imports, which is all an incremental build reuses anyway.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import enum
import io
import json
import os
import re
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Callable, List, Mapping, NamedTuple, Optional, Protocol, Sequence, Tuple

from mypy import build
from mypy.build import BuildSource
from mypy.errors import CompileError, Errors
from mypy.find_sources import InvalidSourceList, create_source_list
from mypy.fscache import FileSystemCache
from mypy.main import process_options
from mypy.modulefinder import matches_exclude
from mypy.options import BuildType, Options
from mypy.util import decode_python_encoding

_PROBE_CACHE = "probes"
"""The cache, inside the owned one, of the probes that check untyped defs.

mypy abandons a module's cache entry when the options it was written under
differ from the build's, and ``check_untyped_defs`` is an option of every
module, ``builtins`` included. Checks and probes taking turns in one cache
each rebuilt everything the other had built: six rounds of a revealed type, a
subtype question and a candidate check over Towel's own source, without its
configuration, took 12.9 s in one cache and 5.7 s in two.
"""


class _Configured(NamedTuple):
    """The project's options, and what mypy said while reading its configuration.

    ``said`` is what mypy's own run prints and then goes on from: an option
    this mypy does not know, a global option in a per-module section. mypy
    exits 0 after saying it, so it is a warning, never a reason to refuse.

    A ``NamedTuple``, not a dataclass: the tests load this file as a module
    that is not in ``sys.modules``, where a dataclass cannot be made.
    """

    options: Options
    said: Tuple[str, ...]


def _read_configuration(config: str | None) -> _Configured:
    """The project's options exactly as its own ``mypy`` reads them, and what mypy said doing so.

    mypy writes every problem it finds in a configuration file and carries
    on (``parse_config_file``: "Errors are written to stderr but are not
    fatal"); only what stops its own run, which exits through ``SystemExit``,
    stops this one. Any output used to be fatal here, so a key mypy 2
    dropped (``force_uppercase_builtins``) or a ``python_version`` in an
    override refused a typed run whose own ``mypy`` said so and checked
    clean. What stops mypy is raised in mypy's words, not as an exit status.
    """
    said = io.StringIO()
    # A synthetic module target suppresses target discovery while parsing the
    # project's options. This API is present throughout mypy 1.x and 2.x. No
    # plugin is loaded until ``_load_configured_plugins``, after the options
    # that would execute or write something have been taken away. Some notices
    # (``--strict-concatenate is deprecated``) are printed to standard output.
    try:
        with redirect_stdout(said):
            _, options = process_options(
                [
                    "--config-file",
                    config or "",
                    "--python-executable",
                    sys.executable,
                    "--module",
                    "__towel_config_probe__",
                ],
                stdout=said,
                stderr=said,
                require_targets=False,
            )
    except SystemExit as stopped:
        raise ValueError(
            said.getvalue().strip() or f"mypy stopped reading its configuration ({stopped.code})"
        ) from None
    return _Configured(options, tuple(line for line in said.getvalue().splitlines() if line))


def _options(root: Path, config: str | None, cache: str, *, probe: bool) -> _Configured:
    """The options of one build: the project's own, but for what a probe needs beyond them.

    A check (a complete build: the baseline, a candidate, the cold
    confirmation) answers for the project, so it runs with the project's
    options -- its configuration, or, where it configures no mypy, mypy's
    defaults, which is what its own ``mypy`` says about the same files. Such a
    project used to be checked with three settings forced on that inference
    had wanted, and each changed the verdict. ``check_untyped_defs`` reported
    errors inside functions without annotations and gave typed code the types
    their assignments infer, where mypy sees ``Any``: dacite, clean under its
    own ``mypy``, was refused for 53 errors in its tests.
    ``ignore_missing_imports`` silenced the imports mypy finds no types for.
    ``explicit_package_bases`` named each module from the project root rather
    than from its packages, ``src/pkg/core.py`` as ``src.pkg.core`` and a test
    as ``tests.test_core``, so neither a test's ``import pkg`` nor its import
    of a helper module beside it found anything, and with the imports
    silenced, an error in that test went unreported.

    A probe (a sparse build: a revealed type, a subtype question) asks what
    type an expression has, not whether the project checks. Inside a function
    without annotations mypy answers ``Any`` for everything unless it checks
    untyped defs, and so it does for an attribute such a function assigns,
    wherever it is read: ``self.items = [1]`` in an unannotated ``__init__`` is
    ``Any`` to every method. A probe of a project that configures no mypy
    therefore checks them, in a cache of its own (see ``_PROBE_CACHE``). A
    configured project's probes run with its own options, as its checks do.
    """
    configured = _read_configuration(config)
    options = configured.options
    options.build_type = BuildType.STANDARD
    if probe and config is None:
        options.check_untyped_defs = True
        cache = os.path.join(cache, _PROBE_CACHE)
    # These settings describe where/how to execute or write, not type rules.
    # The checker is owned by Towel even when its rules come from the project.
    # A plugin is a type rule, not one of these, and stays configured.
    if hasattr(options, "num_workers"):
        options.num_workers = 0  # Own one process; do not leave project-local worker status files.
    options.python_executable = sys.executable
    options.report_dirs = {}
    options.junit_xml = None
    options.timing_stats = None
    options.line_checking_stats = None
    if hasattr(options, "mypyc_annotation_file"):
        options.mypyc_annotation_file = None
    options.cache_dir = cache
    options.incremental = True
    options.show_absolute_path = True
    options.hide_error_codes = True
    options.show_column_numbers = False
    options.show_error_end = False
    # Pretty diagnostics read snippets from disk, but prospective sources and
    # probes exist only in memory and can extend beyond the physical file.
    options.pretty = False
    # Every source, a probed module included, comes with the directory mypy
    # searches for what it imports (its ``base_dir``), and the root is the
    # working directory, searched after them, as in the project's own run.
    # Searched first, the root answered a test's ``import helpers`` with a
    # ``helpers.py`` of its own instead of the one beside the test.
    options.mypy_path = list(
        dict.fromkeys(str((root / path).resolve()) for path in options.mypy_path)
    )
    return _Configured(options, configured.said)


class _PluginUnavailable(Exception):
    """A plugin the project configures cannot be loaded, so no check would be the project's."""


def _load_configured_plugins(options: Options) -> None:
    """Load the project's plugins as its own mypy run loads them, or refuse to check.

    A plugin is a type rule: django-stubs, pydantic and SQLAlchemy each decide
    what an expression's type is. A build without the project's plugins
    answers a question the project never asks, and accepts code its mypy
    rejects. So they are loaded through mypy's own loader, exactly as the
    project's run loads them: a module from this interpreter's environment, a
    ``.py`` path relative to the configuration file, each plugin's entry point
    called with this mypy's version. ``build`` then finds every module already
    imported, and records each plugin's digest in the cache as it always does,
    so a changed plugin invalidates what was concluded under the old one.

    A plugin that cannot be loaded leaves the project's own mypy unable to
    start, and this check refuses in mypy's words rather than check without
    it. Each build is a forked child, so nothing a plugin does at import
    outlives the build it served.

    This is the single place that decides what configured plugins do here.
    """
    if not options.plugins:
        return
    said = io.StringIO()
    try:
        build.load_plugins_from_config(options, Errors(options), said)
    except CompileError as error:
        reason = "; ".join(error.messages) or str(error)
    except Exception as error:  # a plugin's own entry point or constructor raised
        printed = said.getvalue().strip()
        reason = f"{printed + ': ' if printed else ''}{type(error).__name__}: {error}"
    else:
        return
    raise _PluginUnavailable(
        f"mypy could not load a plugin the project configures: {reason}. The project's"
        " mypy loads its plugins before it checks anything, so no check without this one"
        " would be the project's. Make the plugin importable from the interpreter Towel"
        f" runs ({sys.executable}) to check this project."
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Expected a list of strings")
    return [item for item in value if isinstance(item, str)]


def _sources(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Expected sources keyed by absolute path")
    result: dict[str, str] = {}
    for path, text in value.items():
        if not isinstance(path, str) or not isinstance(text, str) or not Path(path).is_absolute():
            raise ValueError("Expected source text and absolute paths")
        result[path] = text
    return result


def _placeholders(value: object) -> dict[str, Tuple[str, str]]:
    """Per probed path, the module name and import directory Towel supplies for when mypy has none."""
    if not isinstance(value, dict):
        raise ValueError("Expected probed module names keyed by absolute path")
    result: dict[str, Tuple[str, str]] = {}
    for path, named in value.items():
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or not isinstance(named, list)
            or len(named) != 2
            or not all(isinstance(part, str) for part in named)
        ):
            raise ValueError("Expected a module name and its directory for each probed path")
        result[path] = (str(named[0]), str(named[1]))
    return result


_ORPHAN_CHECK_SECONDS = 1.0
"""How often a build looks to see whether the worker that wanted it is gone."""

_SUPPLIED_TEXT_RECORD = "towel-supplied-text.jsonl"


def _holds(path: str, text: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return decode_python_encoding(handle.read()) == text
    except (OSError, ValueError):
        return False


def _recorded_paths(record: Path) -> set[str]:
    """The paths in ``record``, forgiving a last line that was cut short.

    A path is recorded before the build that supplies its text, so a process
    killed while appending never reached that build: the cache holds nothing
    written from the missing path's text, and dropping the fragment is sound.
    A damaged line anywhere else cannot be explained that way and is an error.
    """
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    paths: set[str] = set()
    for number, line in enumerate(lines, 1):
        try:
            path = json.loads(line)
        except ValueError:
            if number == len(lines):
                break
            raise ValueError(f"Damaged supplied-text record at line {number}") from None
        if not isinstance(path, str):
            raise ValueError(f"Damaged supplied-text record at line {number}")
        paths.add(path)
    return paths


def _record_paths(record: Path, paths: Sequence[str]) -> None:
    """Append ``paths``, first removing a fragment an interrupted append left behind.

    Appending after a fragment would join it to the first new path, and the
    damage would then sit in the middle of the record, where it is an error.
    """
    try:
        existing = record.read_bytes()
    except FileNotFoundError:
        existing = b""
    if existing and not existing.endswith(b"\n"):
        record.write_bytes(existing[: existing.rfind(b"\n") + 1])
    # A probe cache is made by the first build in it, which this precedes.
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(path) + "\n" for path in paths))


def _disk_text(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return decode_python_encoding(handle.read())
    except (OSError, ValueError):
        return None


def _text_mypy_must_be_given(replacements: Mapping[str, str], cache: str) -> dict[str, str]:
    """The text mypy cannot be left to read from the files, replacement or not.

    mypy consults a module's cache entry only when it reads the module itself;
    supplied text is always parsed and checked again. A complete request
    supplies every analyzed module, nearly all unchanged, so text its file
    already holds is withheld and that module is answered from the cache.

    An entry written from supplied text is the exception. It records the
    *file's* mtime and size beside the *text's* hash, and mypy trusts a matching
    mtime and size without hashing, so it would answer for the file with what it
    concluded about the text. A path once supplied with differing text is
    therefore given text for the rest of the cache's life. The record is written
    before the build, in the cache it describes, so neither a killed build nor a
    replaced worker can separate them.

    The text such a path is given is whatever its file holds now, which is the
    point: a candidate is usually rejected and nothing reaches disk, and the
    next request is sparse and names only the few modules it is about. Giving
    text back only to the paths that request happens to name leaves every other
    poisoned entry answering for its file, so a provider left speculatively
    returning ``str`` was still answering ``str`` while the file on disk
    returned ``int``, and the check came back clean against a project that did
    not exist.
    """
    record = Path(cache) / _SUPPLIED_TEXT_RECORD
    recorded = _recorded_paths(record)
    added = [
        path
        for path, text in replacements.items()
        if path not in recorded and not _holds(path, text)
    ]
    if added:
        _record_paths(record, added)
    given = {path: text for path, text in replacements.items() if path in recorded.union(added)}
    for path in recorded - set(replacements):
        # A file that has since been deleted cannot be restored, and mypy
        # reports its absence, which is the truth about the project.
        text = _disk_text(path)
        if text is not None:
            given[path] = text
    return given


def _is_package(directory: Path) -> bool:
    return (directory / "__init__.py").exists() or (directory / "__init__.pyi").exists()


def _walked_for(path: Path) -> Path:
    """What a complete build walks on behalf of an analyzed file.

    The top package the file belongs to, or the file alone when it belongs to
    none. Walking upwards stops where ``__init__`` does, which is where mypy
    stops when it names the module.
    """
    directory = path.parent
    if not _is_package(directory):
        return path
    while directory.parent != directory and _is_package(directory.parent):
        directory = directory.parent
    return directory


def _complete_targets(
    replacements: Mapping[str, str], options: Options, root: Path, consumers: Sequence[str]
) -> list[str]:
    """What a complete build walks beyond the files being changed.

    A complete build exists to check the modules around the changed ones, whose
    own text is unchanged: a caller of a method whose signature moved, a
    subclass of a host class. Those live in the package under refactoring, and
    mypy follows imports out of it for the rest.

    Walking the project root instead, as this did, checks a repository's every
    other file as well, and a single one that mypy cannot build fails the whole
    request and refuses the project. Repositories are full of them by design:
    test data written to be invalid (``tests/data/cases/comments6.py`` in
    Black, ``testing/data/E11.py`` in pycodestyle), demo and example scripts
    sharing a module name with each other (``setup.py`` twice under pluggy's
    docs), a stub directory beside the package it describes (``src/wrapt-stubs``).
    Seventeen of the first fifty-two projects of the release corpus were refused
    for such a file, none of which any project's own mypy run looks at, and none
    of which can be affected by a change Towel makes. A config that names its
    own ``files`` is still obeyed: there the project has said what it checks.
    """
    if options.files:
        return list(options.files)
    walked = {str(_walked_for(Path(path))) for path in replacements}
    # The caller scans once per project for what imports into those packages,
    # which following imports out of them cannot reach. See ``towel.consumers``.
    walked.update(consumer for consumer in consumers if os.path.exists(consumer))
    return sorted(walked) or [str(root)]


def _named_by_the_project(options: Options) -> set[str]:
    """The ``.py`` files the project's configuration names one by one, not through a directory.

    Naming the file is the one way the project's own mypy checks an
    implementation that has a stub beside it; see :func:`_as_the_project_resolves`.
    """
    return {
        os.path.abspath(entry)
        for entry in options.files or ()
        if entry.endswith(".py") and os.path.isfile(entry)
    }


def _as_the_project_resolves(source: BuildSource, named: set[str]) -> BuildSource:
    """``source``, or the stub beside it when that stub is what the project's mypy reads.

    mypy's walk of a directory keeps ``a.pyi`` and never reads ``a.py``, and an
    import of the module finds the stub first. So a module that ships its own
    stub is the stub to every importer and to the project's own run, and the
    implementation is not checked at all -- unless the configuration names the
    implementation file itself. Towel names every file it changes, and names
    each consumer it scanned for, which on its own would make every such
    module its implementation: an importer of a name the implementation gained
    and the stub lacks was checked against the implementation and called clean,
    while the project's mypy reported the name missing.
    """
    path = source.path
    if path is None or not path.endswith(".py") or os.path.abspath(path) in named:
        return source
    stub = path + "i"
    if not os.path.isfile(stub):
        return source
    return BuildSource(stub, source.module, None, source.base_dir)


def _one_source_per_module(selected: Sequence[BuildSource]) -> list[BuildSource]:
    """The build sources with each file named once.

    A module is reached twice whenever it is both a walked target and a file
    being changed; those are one file and collapse to it. Two different files
    that answer to one module name are kept: ``a/module.py`` and
    ``b/module.py`` where neither directory is a package, or an implementation
    the project names beside its own stub. Collapsing either silently drops a
    file's errors and calls the project clean; mypy's own refusal is the right
    answer there, since it is loud, names both files, and is what the
    project's own run says.
    """
    by_path: dict[str, BuildSource] = {}
    for source in selected:
        if source.path is not None:
            by_path.setdefault(os.path.abspath(source.path), source)
    return list(by_path.values())


def _probed_as_the_project_names(
    path: str, text: Optional[str], placeholder: Tuple[str, str], options: Options
) -> BuildSource:
    """A probed module, named as mypy names its file under the project's configuration.

    Its module name and the directory it imports from are what mypy's own
    walk gives the file (``crawl_up``), so a probe names a module exactly as
    the project's run and a complete check do. Towel's own ``__init__``-chain
    name differed wherever the configuration decides: with
    ``explicit_package_bases`` and ``mypy_path = "src"``, mypy names
    ``src/acme/shop/models.py`` ``acme.shop.models``, a probe named it
    ``shop.models``, and every probe build of such a project failed with
    "Source file found twice". Only a file mypy will not name at all, below a
    package directory whose name is no identifier, takes ``placeholder``.
    """
    try:
        named = create_source_list([path], options)
    except InvalidSourceList:
        named = []
    if len(named) != 1 or not named[0].module:
        module, directory = placeholder
        return BuildSource(path, module, text, directory)
    return BuildSource(path, named[0].module, text, named[0].base_dir)


def _build_sources(
    replacements: Mapping[str, str],
    given: Mapping[str, str],
    options: Options,
    root: Path,
    complete: bool,
    placeholders: Mapping[str, Tuple[str, str]],
    consumers: Sequence[str],
) -> list[BuildSource]:
    # Text is given for the replacements and for every path an earlier request
    # once overlaid, whose cache entry would otherwise answer for its file. The
    # second kind has to be a source of this build as well, or the text has
    # nowhere to be given: a sparse request names a handful of modules, and the
    # poisoned entries are all the others.
    restored = sorted(set(given) - set(replacements))
    named = _named_by_the_project(options)
    # SourceFinder handles namespace packages and .pyi precedence with mypy's
    # own rules. Explicit replacements are included even if config excludes them.
    if not complete:
        # The probed modules are the question and are built from their own
        # text; every other module is read as the project's mypy would read it.
        probed = [
            _probed_as_the_project_names(path, given.get(path), placeholders[path], options)
            for path in replacements
        ]
        return probed + [
            BuildSource(
                resolved.path, resolved.module, given.get(resolved.path or ""), resolved.base_dir
            )
            for resolved in (
                _as_the_project_resolves(source, named)
                for source in create_source_list(restored, options)
            )
            if resolved.path is not None and resolved.path not in replacements
        ]
    selected = create_source_list(list(replacements) + restored, options)
    targets = _complete_targets(replacements, options, root, consumers)
    selected = create_source_list(targets, options, allow_empty_dir=True) + selected
    by_path = {
        os.path.abspath(source.path): source
        for source in _one_source_per_module(
            [_as_the_project_resolves(source, named) for source in selected]
        )
        if source.path is not None
    }
    return [
        BuildSource(path, source.module, given.get(path), source.base_dir)
        for path, source in by_path.items()
        if not Path(path).name.startswith("_towel_probe_")
    ]


_FOUND_TWICE = re.compile(
    r"^(?P<path>.+?): error: Source file found twice under different module names:"
    r' "(?P<given>[^"]+)" and "(?P<imported>[^"]+)"'
)


def _base_for(path: str, module: str) -> Optional[str]:
    """The directory ``module`` is found from when it names the file at ``path``, if it can."""
    file = Path(path)
    spelled = list(file.parent.parts) + ([] if file.stem == "__init__" else [file.stem])
    parts = module.split(".")
    if len(spelled) <= len(parts) or spelled[-len(parts) :] != parts:
        return None
    return str(Path(*spelled[: -len(parts)]))


def _named_by_its_importer(
    messages: Sequence[str],
    sources: Sequence[BuildSource],
    judged: Callable[[str], bool],
    renamed: set[str],
) -> Optional[list[BuildSource]]:
    """``sources`` with one file renamed as the import mypy reached it by names it, if that answers.

    Only a file the project's own run is not given, which exists in that run
    only as the module some import reaches, and only once: every other
    "found twice" is the project's own, or no single rename resolves it.
    """
    for message in messages:
        match = _FOUND_TWICE.match(message)
        if match is None:
            continue
        path = os.path.abspath(match.group("path"))
        for index, source in enumerate(sources):
            if (
                source.path is None
                or os.path.abspath(source.path) != path
                or source.module != match.group("given")
                or path in renamed
                or judged(source.path)
            ):
                continue
            base = _base_for(path, match.group("imported"))
            if base is None:
                return None
            renamed.add(path)
            as_imported = BuildSource(source.path, match.group("imported"), source.text, base)
            return [*sources[:index], as_imported, *sources[index + 1 :]]
    return None


def _build_as_the_project_reaches(
    sources: Sequence[BuildSource],
    options: Options,
    judged: Callable[[str], bool],
    *,
    complete: bool,
) -> Tuple[build.BuildResult, list[BuildSource]]:
    """The build of ``sources``, each file the project reaches only by import taken as it would be.

    Every file Towel changes is a source here, since only a source can be given
    text, and mypy consults ``follow_imports`` only for a module it finds by
    import, never for a source. So a complete build leaves out a file the
    project's run is not given where the project's options do not follow it
    (``skip``, ``error``): its importers then see what the project's run sees,
    ``Any`` or "Import of ... ignored", rather than the types a source would
    give them. A probe keeps it, since its question is the file's own types.

    mypy names a source by its own walk, and such a file exists in the
    project's run only as the module its importer names: ``tools/gen/x.py``,
    outside ``files = ["app"]`` and with no ``__init__`` above it, is ``x`` to
    mypy's walk and ``tools.gen.x`` to ``app/main.py``'s import. Given as
    ``x``, the file was found twice and the build refused, while the project's
    own ``mypy`` checked clean. mypy's own refusal names both names, and such a
    file is given the importer's.
    """
    current = list(sources)
    renamed: set[str] = set()
    while True:
        if complete:
            current = [
                source
                for source in current
                if source.path is None
                or judged(source.path)
                or _followed(options, source.module, source.path) is not _Followed.NOT_FOLLOWED
            ]
        try:
            return build.build(sources=current, options=options), current
        except CompileError as error:
            as_imported = _named_by_its_importer(error.messages, current, judged, renamed)
            if as_imported is None:
                raise
            current = as_imported


def _judged_by_the_project(options: Options, root: Path) -> Callable[[str], bool]:
    """Whether the project's own mypy run would take a file as one of its targets.

    With ``files`` configured, the files those name, found as mypy finds them;
    otherwise every file under ``root`` that no ``exclude`` pattern matches,
    tested as mypy's own walk tests it, on the file and on each directory above
    it. ``options.exclude`` must still be the project's, before Towel adds to it.
    """
    if options.files:
        targets = {
            os.path.abspath(source.path)
            for source in create_source_list(list(options.files), options, allow_empty_dir=True)
            if source.path is not None
        }
        return lambda path: os.path.abspath(path) in targets
    excludes = list(options.exclude)
    cache = FileSystemCache()

    def judged(path: str) -> bool:
        candidate = Path(path)
        if not excludes or not candidate.is_relative_to(root):
            return True
        walked = [candidate, *candidate.parents]
        return not any(
            matches_exclude(str(step), excludes, cache, False)
            for step in walked[: len(candidate.relative_to(root).parts)]
        )

    return judged


class _Followed(enum.Enum):
    """How the project's own run takes a module that a module it checks imports."""

    REPORTED = "followed, and its errors reported"
    SILENCED = "followed, and its errors silenced"
    NOT_FOLLOWED = "not followed"


def _followed(options: Options, module: str, path: Optional[str]) -> _Followed:
    """How the project's own run takes an import of ``module``, as that module's options say.

    mypy decides it for each module it is asked to import
    (``find_module_and_diagnose``) from the module's own options: the global
    ``follow_imports`` as every section whose pattern names the module leaves
    it (``Options.clone_for_module``), except that a stub is followed and
    reported unless ``follow_imports_for_stubs`` says otherwise.
    """
    own = options.clone_for_module(module)
    if path is not None and path.endswith(".pyi") and not own.follow_imports_for_stubs:
        return _Followed.REPORTED
    if own.follow_imports in {"skip", "error"}:
        return _Followed.NOT_FOLLOWED
    return _Followed.SILENCED if own.follow_imports == "silent" else _Followed.REPORTED


_MESSAGE_PATH = re.compile(r"^(?P<path>.*?):(?:\d+:)?(?:\d+:)? (?:error|note|warning): ")


class _Imports(Protocol):
    """What a module of a finished build imports: all this judgement reads of mypy's ``State``."""

    @property
    def dependencies(self) -> Sequence[str]:
        """The modules it imports that the build found."""

    @property
    def ancestors(self) -> Optional[Sequence[str]]:
        """The packages above it, which mypy loads with it."""

    @property
    def path(self) -> Optional[str]:
        """Its file."""


def _as_the_project_judges(
    messages: Sequence[str],
    graph: Mapping[str, _Imports],
    sources: Sequence[BuildSource],
    judged: Callable[[str], bool],
    options: Options,
) -> list[str]:
    """``messages`` without those about files the project's own mypy run never checks.

    Towel analyses what it is pointed at, ``tests/`` included, and every file it
    analyses is a source of the build; a configuration excluding ``tests/``, or
    naming ``files`` without it, then had its tests checked here and nowhere
    else, and a project whose mypy run is clean was refused. A file the project
    does not name is still checked when a file it does name imports it, since
    mypy follows that import and reports what it finds -- unless that module's
    own options silence it or do not follow it (:func:`_followed`). Reading
    only the global ``follow_imports`` dropped every error in ``tools/`` under
    a global ``silent`` whose ``tools.*`` section said ``normal``, and a helper
    the project's own ``mypy`` rejected there was accepted.
    """
    unjudged = {
        source.module: os.path.abspath(source.path)
        for source in sources
        if source.path is not None and not judged(source.path)
    }
    if not unjudged:
        return list(messages)
    checked = [source.module for source in sources if source.module not in unjudged]
    seen = set(checked)
    reported: set[str] = set()
    pending = list(checked)
    while pending:
        state = graph.get(pending.pop())
        if state is None:
            continue
        for dependency in [*state.dependencies, *(state.ancestors or [])]:
            if dependency in seen:
                continue
            seen.add(dependency)
            imported = graph.get(dependency)
            how = _followed(options, dependency, imported.path if imported is not None else None)
            if how is _Followed.NOT_FOLLOWED:
                continue
            if how is _Followed.REPORTED:
                reported.add(dependency)
            pending.append(dependency)
    unchecked = {path for module, path in unjudged.items() if module not in reported}

    def about_unchecked(message: str) -> bool:
        match = _MESSAGE_PATH.match(message)
        return match is not None and os.path.abspath(match.group("path")) in unchecked

    return [message for message in messages if not about_unchecked(message)]


class _Answered(NamedTuple):
    """A build's diagnostics, and what mypy said about the configuration on the way."""

    messages: List[str]
    said: Tuple[str, ...]


def _request(request: object, cache: str) -> _Answered:
    if not isinstance(request, dict):
        raise ValueError("Expected a request object")
    root_value = request.get("root")
    config = request.get("config")
    if not isinstance(root_value, str) or not (config is None or isinstance(config, str)):
        raise ValueError("Invalid checker root or config")
    root = Path(root_value)
    os.chdir(root)
    complete = request.get("complete") is True
    configured = _options(root, config, cache, probe=not complete)
    options = configured.options
    judged = _judged_by_the_project(options, root)
    for excluded in _strings(request.get("excluded_paths")):
        path = Path(excluded)
        spellings = [str(path)]
        if path.is_relative_to(root):
            spellings.append(str(path.relative_to(root)))
        options.exclude += ["^" + re.escape(spelling) + r"(?:/|$)" for spelling in spellings]
    _load_configured_plugins(options)
    replacements = _sources(request.get("sources"))
    sources = _build_sources(
        replacements,
        # The record describes the entries of the cache this build uses.
        _text_mypy_must_be_given(replacements, options.cache_dir),
        options,
        root,
        complete,
        _placeholders(request.get("modules")),
        _strings(request.get("consumers") or []),
    )
    result, sources = _build_as_the_project_reaches(sources, options, judged, complete=complete)
    if not complete:
        return _Answered(list(result.errors), configured.said)
    return _Answered(
        _as_the_project_judges(result.errors, result.graph, sources, judged, options),
        configured.said,
    )


def _answer(line: str, cache: str) -> str:
    answered = _Answered([], ())
    failure: str | None = None
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            answered = _request(json.loads(line), cache)
    except _PluginUnavailable as error:
        failure = str(error)
    except (Exception, SystemExit) as error:
        failure = f"{type(error).__name__}: {error}"
    return (
        json.dumps(
            {"messages": answered.messages, "failure": failure, "warnings": list(answered.said)}
        )
        + "\n"
    )


def _exit_with_parent(owner: int) -> None:
    """End this process once ``owner`` is gone.

    A build holds a core and gigabytes, and a worker killed outright cannot
    stop it: the signal that would have been passed on is never delivered.
    The child is reparented when its parent dies, which is what this watches
    for, so an unanswerable build does not outlive the run that wanted it.
    """
    while os.getppid() == owner:
        time.sleep(_ORPHAN_CHECK_SECONDS)
    os._exit(1)


def _serve_in_child(line: str, cache: str) -> None:
    """Answer one request from a forked child; report a child that died without answering."""
    output = sys.stdout
    owner = os.getpid()
    child = os.fork()
    if child == 0:
        status = 1
        try:
            watcher = threading.Thread(target=_exit_with_parent, args=(owner,), daemon=True)
            watcher.start()
            output.write(_answer(line, cache))
            output.flush()
            status = 0
        finally:
            os._exit(status)  # Never return into the parent's loop or run its cleanup.

    def stop_child(signum: int, frame: object) -> None:
        os.kill(child, signal.SIGKILL)
        os._exit(1)

    previous = signal.signal(signal.SIGTERM, stop_child)
    try:
        _, status = os.waitpid(child, 0)
    finally:
        signal.signal(signal.SIGTERM, previous)
    if status != 0:
        failure = f"mypy build process ended with wait status {status}"
        output.write(json.dumps({"messages": [], "failure": failure}) + "\n")
        output.flush()


def main() -> None:
    """Serve serialized builds until the parent closes its pipe."""
    if len(sys.argv) != 2:
        raise SystemExit("Expected the owned cache directory")
    for line in sys.stdin:
        _serve_in_child(line, sys.argv[1])


if __name__ == "__main__":
    main()
