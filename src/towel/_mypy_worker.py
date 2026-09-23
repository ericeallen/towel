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
import io
import json
import os
import re
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Callable, Mapping, Optional, Sequence

from mypy import build
from mypy.build import BuildSource
from mypy.errors import CompileError, Errors
from mypy.find_sources import create_source_list
from mypy.fscache import FileSystemCache
from mypy.main import process_options
from mypy.modulefinder import matches_exclude
from mypy.options import BuildType, Options
from mypy.util import decode_python_encoding


def _options(root: Path, config: str | None, cache: str, roots: Sequence[str]) -> Options:
    errors = io.StringIO()
    # A synthetic module target suppresses target discovery while parsing the
    # project's options. This API is present throughout mypy 1.x and 2.x. No
    # plugin is loaded until ``_load_configured_plugins``, after the options
    # below that would execute or write something have been taken away.
    _, options = process_options(
        [
            "--config-file",
            config or "",
            "--python-executable",
            sys.executable,
            "--module",
            "__towel_config_probe__",
        ],
        stdout=io.StringIO(),
        stderr=errors,
        require_targets=False,
    )
    if errors.getvalue():
        raise ValueError(errors.getvalue().strip())
    options.build_type = BuildType.STANDARD
    if config is None:
        options.ignore_missing_imports = True
        options.check_untyped_defs = True
        options.explicit_package_bases = True
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
    options.mypy_path = list(
        dict.fromkeys(
            [
                *(str((root / path).resolve()) for path in options.mypy_path),
                *roots,
            ]
        )
    )
    return options


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


def _implementation_of_stub(paths: Sequence[str]) -> Optional[str]:
    """The implementation of a stub-and-implementation pair, or ``None`` for anything else.

    The only two files that may answer to one module name without a loss: a
    ``.pyi`` and the ``.py`` beside it, same directory and same stem. That is a
    package shipping stubs for itself, which attrs and more-itertools both do.
    Two unrelated files that merely infer the same module name are not this, and
    collapsing them would drop one file's errors and report the project clean.
    """
    if len(paths) != 2:
        return None
    ordered = sorted(paths, key=lambda path: path.endswith(".pyi"))
    implementation, stub = ordered
    if not stub.endswith(".pyi") or not implementation.endswith(".py"):
        return None
    if Path(stub).with_suffix(".py") != Path(implementation):
        return None
    return implementation


def _one_source_per_module(selected: Sequence[BuildSource]) -> list[BuildSource]:
    """The build sources with each module named once where naming it twice is a duplicate.

    A module is reached twice whenever it is both a walked target and a file
    being changed; those are one file and collapse to it. A stub and the
    implementation beside it are two files, and mypy refuses a build naming one
    module twice, so a package shipping stubs for itself was refused outright.
    That pair collapses to the implementation, because that is the file Towel is
    changing and the one that has to be checked; the choice is the same before
    and after the change, which is what the invariant needs.

    Anything else keeps every file. Two unrelated modules can infer one name --
    ``a/module.py`` and ``b/module.py``, neither directory a package -- and
    collapsing those silently drops the second file's errors and calls the
    project clean. mypy's own refusal is the right answer there: it is loud, it
    names both files, and Towel turns it into a refusal that says what to do.
    """
    by_module: dict[str, list[BuildSource]] = {}
    for source in selected:
        if source.path is not None:
            by_module.setdefault(source.module, []).append(source)
    kept: list[BuildSource] = []
    for sources in by_module.values():
        by_path = {os.path.abspath(source.path or ""): source for source in sources}
        if len(by_path) > 1:
            implementation = _implementation_of_stub(sorted(by_path))
            if implementation is not None:
                by_path = {implementation: by_path[implementation]}
        kept.extend(by_path.values())
    return kept


def _build_sources(
    replacements: Mapping[str, str],
    given: Mapping[str, str],
    options: Options,
    root: Path,
    complete: bool,
    modules: Mapping[str, str],
    consumers: Sequence[str],
) -> list[BuildSource]:
    # Text is given for the replacements and for every path an earlier request
    # once overlaid, whose cache entry would otherwise answer for its file. The
    # second kind has to be a source of this build as well, or the text has
    # nowhere to be given: a sparse request names a handful of modules, and the
    # poisoned entries are all the others.
    restored = sorted(set(given) - set(replacements))
    # SourceFinder handles namespace packages and .pyi precedence with mypy's
    # own rules. Explicit replacements are included even if config excludes them.
    if not complete:
        named = [BuildSource(path, modules[path], given.get(path)) for path in replacements]
        return named + [
            BuildSource(source.path, source.module, given.get(source.path or ""), source.base_dir)
            for source in create_source_list(restored, options)
            if source.path is not None and source.path not in replacements
        ]
    selected = create_source_list(list(replacements) + restored, options)
    targets = _complete_targets(replacements, options, root, consumers)
    selected = create_source_list(targets, options, allow_empty_dir=True) + selected
    by_path = {
        os.path.abspath(source.path): source
        for source in _one_source_per_module(selected)
        if source.path is not None
    }
    return [
        BuildSource(path, source.module, given.get(path), source.base_dir)
        for path, source in by_path.items()
        if not Path(path).name.startswith("_towel_probe_")
    ]


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


_MESSAGE_PATH = re.compile(r"^(?P<path>.*?):(?:\d+:)?(?:\d+:)? (?:error|note|warning): ")


def _as_the_project_judges(
    messages: Sequence[str],
    result: build.BuildResult,
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
    mypy follows that import and reports what it finds -- unless the
    configuration silences followed imports, when it reports nothing there.
    """
    unjudged = {
        source.module: os.path.abspath(source.path)
        for source in sources
        if source.path is not None and not judged(source.path)
    }
    if not unjudged:
        return list(messages)
    reached: set[str] = set()
    if options.follow_imports not in {"silent", "skip"}:
        pending = [source.module for source in sources if source.module not in unjudged]
        while pending:
            state = result.graph.get(pending.pop())
            if state is None:
                continue
            for dependency in [*state.dependencies, *(state.ancestors or [])]:
                if dependency not in reached:
                    reached.add(dependency)
                    pending.append(dependency)
    unchecked = {path for module, path in unjudged.items() if module not in reached}

    def about_unchecked(message: str) -> bool:
        match = _MESSAGE_PATH.match(message)
        return match is not None and os.path.abspath(match.group("path")) in unchecked

    return [message for message in messages if not about_unchecked(message)]


def _request(request: object, cache: str) -> list[str]:
    if not isinstance(request, dict):
        raise ValueError("Expected a request object")
    root_value = request.get("root")
    config = request.get("config")
    if not isinstance(root_value, str) or not (config is None or isinstance(config, str)):
        raise ValueError("Invalid checker root or config")
    root = Path(root_value)
    os.chdir(root)
    options = _options(root, config, cache, _strings(request.get("roots")))
    complete = request.get("complete") is True
    judged = _judged_by_the_project(options, root) if complete else None
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
        _text_mypy_must_be_given(replacements, cache),
        options,
        root,
        complete,
        _sources(request.get("modules")),
        _strings(request.get("consumers") or []),
    )
    result = build.build(sources=sources, options=options)
    if judged is None:
        return list(result.errors)
    return _as_the_project_judges(result.errors, result, sources, judged, options)


def _answer(line: str, cache: str) -> str:
    messages: list[str] = []
    failure: str | None = None
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            messages = _request(json.loads(line), cache)
    except _PluginUnavailable as error:
        failure = str(error)
    except (Exception, SystemExit) as error:
        failure = f"{type(error).__name__}: {error}"
    return json.dumps({"messages": messages, "failure": failure}) + "\n"


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
