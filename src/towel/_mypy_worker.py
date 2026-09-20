"""Run mypy in an owned process so its global state cannot change the caller.

The line-delimited protocol contains source text and checker diagnostics only.
Project plugins and configured executables are never loaded. This file is
launched by its absolute installed path with ``python -I``.

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
from typing import Mapping, Sequence

from mypy import build
from mypy.build import BuildSource
from mypy.find_sources import create_source_list
from mypy.main import process_options
from mypy.options import BuildType, Options
from mypy.util import decode_python_encoding


def _options(root: Path, config: str | None, cache: str, roots: Sequence[str]) -> Options:
    errors = io.StringIO()
    # A synthetic module target suppresses target discovery while parsing the
    # project's options. This API is present throughout mypy 1.x and 2.x. No
    # plugin is loaded until build(), after the unsafe options below are removed.
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
    options.plugins = []
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


def _text_mypy_must_be_given(replacements: Mapping[str, str], cache: str) -> dict[str, str]:
    """The replacements mypy cannot be left to read from their files.

    mypy consults a module's cache entry only when it reads the module itself;
    supplied text is always parsed and checked again. A complete request
    supplies every analyzed module, nearly all unchanged, so text its file
    already holds is withheld and that module is answered from the cache.

    An entry written from supplied text is the exception. It records the
    *file's* mtime and size beside the *text's* hash, and mypy trusts a matching
    mtime and size without hashing, so it would answer for the file with what it
    concluded about the text. A path once supplied with differing text is
    therefore supplied for the rest of the cache's life. The record is written
    before the build, in the cache it describes, so neither a killed build nor a
    replaced worker can separate them.
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
    return {path: text for path, text in replacements.items() if path in recorded.union(added)}


def _build_sources(
    replacements: Mapping[str, str],
    given: Mapping[str, str],
    options: Options,
    root: Path,
    complete: bool,
    modules: Mapping[str, str],
) -> list[BuildSource]:
    # SourceFinder handles namespace packages and .pyi precedence with mypy's
    # own rules. Explicit replacements are included even if config excludes them.
    if not complete:
        return [BuildSource(path, modules[path], given.get(path)) for path in replacements]
    selected = create_source_list(list(replacements), options)
    if complete:
        targets = options.files or [str(root)]
        selected = create_source_list(targets, options, allow_empty_dir=True) + selected
    by_path = {
        os.path.abspath(source.path): source for source in selected if source.path is not None
    }
    return [
        BuildSource(path, source.module, given.get(path), source.base_dir)
        for path, source in by_path.items()
        if not Path(path).name.startswith("_towel_probe_")
    ]


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
    for excluded in _strings(request.get("excluded_paths")):
        path = Path(excluded)
        spellings = [str(path)]
        if path.is_relative_to(root):
            spellings.append(str(path.relative_to(root)))
        options.exclude += ["^" + re.escape(spelling) + r"(?:/|$)" for spelling in spellings]
    replacements = _sources(request.get("sources"))
    sources = _build_sources(
        replacements,
        _text_mypy_must_be_given(replacements, cache),
        options,
        root,
        request.get("complete") is True,
        _sources(request.get("modules")),
    )
    return list(build.build(sources=sources, options=options).errors)


def _answer(line: str, cache: str) -> str:
    messages: list[str] = []
    failure: str | None = None
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            messages = _request(json.loads(line), cache)
    except (Exception, SystemExit) as error:
        failure = f"{type(error).__name__}: {error}"
    return json.dumps({"messages": messages, "failure": failure}) + "\n"


def _serve_in_child(line: str, cache: str) -> None:
    """Answer one request from a forked child; report a child that died without answering."""
    output = sys.stdout
    child = os.fork()
    if child == 0:
        status = 1
        try:
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
