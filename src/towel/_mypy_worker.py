"""Run mypy in an owned process so its global state cannot change the caller.

The line-delimited protocol contains source text and checker diagnostics only.
Project plugins and configured executables are never loaded. This file is
launched by its absolute installed path with ``python -I``.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import gc
import io
import json
import os
import re
from pathlib import Path
import sys
from typing import Mapping, Sequence

from mypy import build
from mypy.build import BuildSource
from mypy.find_sources import create_source_list
from mypy.main import process_options
from mypy.options import BuildType, Options


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


def _build_sources(
    replacements: Mapping[str, str],
    options: Options,
    root: Path,
    complete: bool,
    modules: Mapping[str, str],
) -> list[BuildSource]:
    # SourceFinder handles namespace packages and .pyi precedence with mypy's
    # own rules. Explicit replacements are included even if config excludes them.
    if not complete:
        return [BuildSource(path, modules[path], text) for path, text in replacements.items()]
    selected = create_source_list(list(replacements), options)
    if complete:
        targets = options.files or [str(root)]
        selected = create_source_list(targets, options, allow_empty_dir=True) + selected
    by_path = {
        os.path.abspath(source.path): source for source in selected if source.path is not None
    }
    return [
        BuildSource(path, source.module, replacements.get(path), source.base_dir)
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
        options,
        root,
        request.get("complete") is True,
        _sources(request.get("modules")),
    )
    return list(build.build(sources=sources, options=options).errors)


def main() -> None:
    """Serve serialized builds until the parent closes its pipe."""
    if len(sys.argv) != 2:
        raise SystemExit("Expected the owned cache directory")
    cache = sys.argv[1]
    output = sys.stdout
    for number, line in enumerate(sys.stdin, 1):
        messages: list[str] = []
        failure: str | None = None
        captured = io.StringIO()
        try:
            with redirect_stdout(captured), redirect_stderr(captured):
                messages = _request(json.loads(line), cache)
        except (Exception, SystemExit) as error:
            failure = f"{type(error).__name__}: {error}"
        # Only this worker's heap is collected. No freeze spans work in the
        # calling application, and terminating the worker releases all graphs.
        if number % 10 == 0:
            gc.collect()
        output.write(json.dumps({"messages": messages, "failure": failure}) + "\n")
        output.flush()


if __name__ == "__main__":
    main()
