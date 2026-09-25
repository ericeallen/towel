#!/usr/bin/env python3
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

"""
Command-line interface for Towel.

This module provides CLI entry points for the Towel code refactoring tool.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
import os
import re
import signal
import sys
import tempfile
import textwrap
from importlib.metadata import version
from pathlib import Path
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    TYPE_CHECKING,
    Tuple,
    TypedDict,
)

if TYPE_CHECKING:
    from towel.import_model import ImportModel, ImportProblem, MissingModule
    from towel.type_inference import TypeOracle
    from towel.unification.fixed_point import RunReport
    from towel.unification.models import RefactoringProposal
    from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.changes import apply_changes, journals_covering, pending_journal_remedy, recover
from towel.diagnostics import LOG, Settings, configure_stderr_logging
from towel.program_files import is_exclusion_name, refuse_unparsed_program
from towel.unification.exceptions import TowelError
from towel.source_text import read_source, source_lines
from towel.source_files import python_sources
from towel.unification.class_private import (
    class_qualnames,
    is_class_private,
    mangling_classes,
    mangling_prefix,
)
from towel.unification.models import GENERATED_HELPER_NAME, ParameterKind
from towel.unification.defaults import (
    DEFAULT_MAX_CANDIDATE_PAIRS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_PARAMETERS,
    DEFAULT_MIN_LINES,
)
from towel.unification.progress import DEFAULT_PROGRESS, ProgressMode, normalize_progress


def _build_parser() -> argparse.ArgumentParser:
    """The command-line parser, shared by ``main`` and the tests."""
    parser = argparse.ArgumentParser(
        prog="towel",
        description="A Python tool that DRYs your code - finds and refactors repeated code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  towel preview src/                    # Preview duplicates (read-only)
  towel dry src/ cleaned/               # Apply refactorings to new directory
  towel dry src/ src/                   # Apply refactorings in-place
  towel rename-helpers                  # Rename extracted functions with LLM assistance

For more help on a specific command:
  towel <command> --help
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"towel {version('code-towel')}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    _add_dry_parser(subparsers)
    _add_preview_parser(subparsers)
    _add_rename_helpers_parser(subparsers)
    recovery = subparsers.add_parser("recover", help="Roll back an interrupted local transaction")
    recovery.add_argument(
        "journal",
        type=Path,
        help="the .towel-transaction-<id> directory an interrupted run left behind",
    )
    return parser


def main() -> None:
    """Main entry point with subcommands."""
    _install_signal_handling()
    parser = _build_parser()
    args = parser.parse_args()
    configure_stderr_logging()
    _settings().enable_debug_logging()
    try:
        _dispatch(parser, args)
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted.\n")
    except BrokenPipeError:
        # The reader went away (``towel preview ... | head``); nothing is wrong.
        _close_stdout_quietly()
        sys.exit(0)


_SETTINGS: Optional[Settings] = None


def _settings() -> Settings:
    """The process's settings, read from the environment once."""
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings.from_environ()
    return _SETTINGS


def _install_signal_handling() -> None:
    """Make SIGTERM an orderly exit, so temporary files and journals are cleaned up.

    The default handler kills the process outright, leaving the mypy cache
    directory, a partial copy, and any pyright probe behind; raising
    SystemExit runs the context managers and the atexit hooks that remove
    them. Standard output is reconfigured so a non-ASCII path or source
    line cannot fail the run under an ASCII locale.
    """
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


def _close_stdout_quietly() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:
        pass


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "dry":
        try:
            _run_dry(args)
        except BrokenPipeError:
            raise
        except (OSError, ValueError, TowelError) as error:
            parser.exit(1, f"Error: {error}\n")
    elif args.command == "preview":
        try:
            _run_preview(args)
        except BrokenPipeError:
            raise
        except (OSError, ValueError, TowelError) as error:
            parser.exit(1, f"Error: {error}\n")
    elif args.command == "rename-helpers":
        try:
            _run_rename_helpers(args)
        except BrokenPipeError:
            raise
        except (OSError, ValueError, SyntaxError, TowelError) as error:
            parser.exit(1, f"Error: {error}\n")
    elif args.command == "recover":
        try:
            recover(args.journal)
        except BrokenPipeError:
            raise
        except (OSError, ValueError) as error:
            parser.exit(1, f"Error: {error}\n")


def _count(text: str) -> int:
    """An argparse type for a count: a whole number that is not negative."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"{value} is negative; 0 runs to a fixed point")
    return value


def _add_import_layout_flags(parser: argparse.ArgumentParser) -> None:
    """Accept the retired import-layout flags, --prefer-absolute-imports and --pep420.

    They chose how a cross-file helper's import was spelled from packaging
    metadata. Since 1.772 every import is spelled as the program's own
    imports show it works, so they decide nothing; scripts that pass them
    keep working, the help no longer lists them, and a run that is given
    one says it had no effect (:func:`_warn_about_retired_flags`).
    """
    for flag, dest in (
        ("--prefer-absolute-imports", "prefer_absolute_imports"),
        ("--pep420", "pep420"),
    ):
        parser.add_argument(  # retired in 1.772, kept for scripts
            flag,
            dest=dest,
            action=argparse.BooleanOptionalAction,
            default=None,
            help=argparse.SUPPRESS,
        )


def _warn_about_retired_flags(args: argparse.Namespace) -> None:
    """Say that a retired import-layout flag was given and changed nothing."""
    given = [
        flag
        for flag, dest in (
            ("--prefer-absolute-imports", "prefer_absolute_imports"),
            ("--pep420", "pep420"),
        )
        if getattr(args, dest, None) is not None
    ]
    if given:
        LOG.warning(
            "%s no longer has any effect: every import Towel writes is spelled as the program's"
            " own imports show it works.",
            " and ".join(given),
        )


def _excluded_name(value: str) -> str:
    """An argparse type for ``--exclude``: a directory's or a file's name, matched at any depth.

    A path or a pattern matches no name, so given one the flag would leave
    out nothing, silently; it is refused, naming what to pass instead: the
    last part of a path, whether it names a directory or a file. A trailing
    separator, as a shell completes a directory, is dropped.
    """
    name = value.rstrip("/" + os.sep)
    if is_exclusion_name(name):
        return name
    last = Path(name).name if name else ""
    instead = f"; for {value}, pass --exclude {last}" if is_exclusion_name(last) else ""
    raise argparse.ArgumentTypeError(
        "--exclude takes the name of a directory or a file, not a path or a pattern, and leaves"
        f" out every directory or file of that name{instead}"
    )


def _add_exclude_flag(parser: argparse.ArgumentParser) -> None:
    """Add ``--exclude``, shared by the commands that analyze a directory."""
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=_excluded_name,
        metavar="NAME",
        help="Name of a directory or file to leave unchanged (repeatable), at any depth, e.g."
        " tests or benchmark.py; checks that read the whole program still read it, and one"
        " there that does not parse is taken for no part of the program",
    )


def _add_cross_module_flag(parser: argparse.ArgumentParser) -> None:
    """Add ``--cross-module``, shared by the commands that analyze a project.

    Off by default: a helper shared across modules adds an import between
    them, a change to how the project's modules depend on each other that a
    user should ask for rather than find in the diff.
    """
    parser.add_argument(
        "--cross-module",
        dest="cross_module",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Also share a helper between duplicates in different modules, importing it from "
        "the module that hosts it into the others, spelled as the program's own imports "
        "show that import works; a run refuses when those imports leave the names of the "
        "package it refactors in doubt. --no-cross-module, the default, extracts only within "
        "a module and adds no import between the project's modules that runs.",
    )


def _add_parameterize_builtins_flag(parser: argparse.ArgumentParser) -> None:
    """Add ``--parameterize-builtins``, shared by the commands that analyze a project.

    Off by default: no helper takes a builtin as a parameter, since a call
    such as ``helper(rows, len)`` would surprise its reader.
    """
    parser.add_argument(
        "--parameterize-builtins",
        dest="parameterize_builtins",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Where a builtin the duplicated code reads may differ between its sites (one "
        "function binds len, the other reads the builtin; or, with --cross-module, a module "
        "may hold the name), pass it to the helper as a parameter instead of declining the "
        "pair. --no-parameterize-builtins, the default, gives no helper a builtin parameter.",
    )


def _add_dry_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Add 'dry' subcommand parser."""
    parser = subparsers.add_parser(
        "dry",
        help="Apply refactorings to remove duplicate code",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Analyze and apply refactorings using anti-unification to files or directories.",
        epilog="""
Examples:
  towel dry src/ cleaned/                         # Refactor directory until fixed point
  towel dry file.py file_out.py --no-interactive  # Skip the confirmation prompt
  towel dry src/ out/ --max-refactorings 50       # Stop after 50 applied refactorings
  towel dry src/ out/ --progress detail           # Verbose per-phase output
        """,
    )

    parser.add_argument("input", help="Input file or directory")
    parser.add_argument("output", help="Output file or directory (can be same as input)")
    parser.add_argument(
        "--interactive",
        dest="interactive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ask for confirmation before writing (the default); --no-interactive skips the prompt",
    )
    parser.add_argument(  # earlier spelling, kept for scripts
        "--non-interactive", dest="interactive", action="store_false", help=argparse.SUPPRESS
    )
    _add_exclude_flag(parser)
    _add_cross_module_flag(parser)
    _add_parameterize_builtins_flag(parser)

    _add_import_layout_flags(parser)

    parser.add_argument(
        "--max-refactorings",
        dest="max_refactorings",
        type=_count,
        default=DEFAULT_MAX_ITERATIONS,
        metavar="N",
        help="Stop after N applied refactorings (0, the default, runs to a fixed point)",
    )
    _add_tuning_flags(parser)
    parser.add_argument(  # earlier spelling, kept for scripts
        "--max-iterations",
        dest="max_refactorings",
        type=_count,
        metavar="N",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--types",
        dest="types",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Annotate generated helpers (the default), in code that already uses annotations: "
        "the annotations the call sites declare, and the types the project's checker (mypy or "
        "pyright, the 'types' extra) infers and verifies for the rest. --no-types leaves "
        "helpers bare.",
    )
    parser.add_argument(
        "--format",
        dest="format",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Format generated code (the default) with the project's formatter (ruff when "
        "configured, else Black; the 'format' extra) at the line length the project declares, "
        "and sort inserted imports the way the project does (ruff's I rules or isort). "
        "--no-format inserts code as rendered.",
    )

    _add_progress_flag(
        parser,
        detail="'detail' logs each pass's discovered proposals and follow-ups instead of a bar",
    )


def _add_tuning_flags(parser: argparse.ArgumentParser) -> None:
    """The engine knobs the dry and preview commands share."""
    parser.add_argument(
        "--min-lines",
        type=_count,
        default=DEFAULT_MIN_LINES,
        metavar="N",
        help=f"Fewest source lines a duplicated block may span (default: {DEFAULT_MIN_LINES})",
    )
    parser.add_argument(
        "--max-parameters",
        type=_count,
        default=DEFAULT_MAX_PARAMETERS,
        metavar="N",
        help=f"Most parameters an extracted helper may take (default: {DEFAULT_MAX_PARAMETERS})",
    )
    parser.add_argument(
        "--max-pairs",
        type=_count,
        default=DEFAULT_MAX_CANDIDATE_PAIRS,
        metavar="N",
        help=(
            "Most candidate block pairs one analysis evaluates; past it the largest groups of "
            f"similar blocks are left out with a warning (default: {DEFAULT_MAX_CANDIDATE_PAIRS})"
        ),
    )


def _add_progress_flag(parser: argparse.ArgumentParser, detail: str) -> None:
    """Add ``--progress``, shared by the commands that analyze a project.

    ``detail`` says what the ``detail`` mode does for this command: the
    fixed-point driver logs per pass, a single analysis only drops the bar.
    """
    parser.add_argument(
        "--progress",
        choices=["auto", "tqdm", "none", "detail"],
        default=DEFAULT_PROGRESS,
        help="Progress display mode: 'tqdm' shows bars (the default), 'auto' falls back if tqdm "
        f"is unavailable, 'none' disables output, {detail}.",
    )


def _add_preview_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Add 'preview' subcommand parser."""
    parser = subparsers.add_parser(
        "preview",
        help="Preview duplicate code detection (read-only)",
        description="Preview refactoring opportunities using anti-unification without modifying files.",
    )

    parser.add_argument("target", help="File or directory to analyze")
    _add_exclude_flag(parser)
    _add_cross_module_flag(parser)
    _add_parameterize_builtins_flag(parser)
    _add_tuning_flags(parser)
    _add_progress_flag(
        parser,
        detail="'detail' shows no bar either (a preview is one analysis, with no passes to log)",
    )

    _add_import_layout_flags(parser)


def _add_rename_helpers_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Add 'rename-helpers' subcommand parser."""
    parser = subparsers.add_parser(
        "rename-helpers",
        help="Rename extracted functions using LLM assistance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Rename auto-generated _extracted_func_* and __extracted_func_* helpers into meaningful names.

This tool analyzes the extracted functions and uses an LLM to suggest better names
based on the code's purpose and context. You can use Claude Code, ChatGPT, or any
other AI coding assistant to generate the names.

The tool works in two modes:

1. INTERACTIVE MODE (default):
   Generates a prompt for an LLM to review your code and suggest new names.
   You paste the LLM's response back, and the tool applies the renamings.

2. FILE MODE (--rename-file):
   Provide a JSON file with old_name -> new_name mappings.
   The tool applies these renamings across your entire codebase.
        """,
        epilog="""
Examples:
  # Interactive mode: generate LLM prompt and apply suggestions
  towel rename-helpers src/

  # List all extracted helpers
  towel rename-helpers src/ --list

  # Apply renamings from a JSON file
  towel rename-helpers src/ --rename-file renames.json

  # Show the renames without writing files
  towel rename-helpers src/ --rename-file renames.json --preview

  # Specify specific files or functions
  towel rename-helpers src/ --file mymodule.py
  towel rename-helpers src/ --function __extracted_func_7
        """,
    )

    parser.add_argument(
        "target",
        help="Directory containing generated extracted helper functions",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List all extracted helper functions found",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "With --list, print a JSON inventory of every helper: scope, parameters with "
            "their evaluation kind, source, call sites, and the mapping keys that rename them. "
            "With --rename-file, print a JSON result; a rejected mapping exits with status 2 "
            "and the reason"
        ),
    )
    parser.add_argument(
        "--preview",
        dest="preview",
        action="store_true",
        help="Show the renames without writing files",
    )
    parser.add_argument(  # earlier spelling, kept for scripts
        "--dry-run", dest="preview", action="store_true", help=argparse.SUPPRESS
    )

    parser.add_argument(
        "--rename-file",
        type=Path,
        help="JSON file with old_name -> new_name mappings to apply",
    )

    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        help="Limit processing to specific file(s) (relative path)",
    )

    parser.add_argument(
        "--function",
        action="append",
        dest="functions",
        help="Limit processing to specific function(s) by name",
    )

    parser.add_argument(
        "--llm",
        choices=["claude", "gpt", "copilot", "generic"],
        default="generic",
        help="Format the LLM prompt for a specific AI assistant (default: generic)",
    )


CHANGE_SIDECAR_NAME = ".towel-helpers.json"


class ChangeRecord(TypedDict):
    """One rewritten call site as the sidecar and the inventory record it."""

    file: str
    line: int
    before: str
    after: str


class CallRecord(TypedDict):
    """A call of a generated helper as found in the refactored tree."""

    file: str
    line: int
    bound: bool
    statement: str
    arguments: List[str]


class BindingRecord(TypedDict):
    """The argument expression one call site passes for a parameter."""

    file: str
    line: int
    expression: str


class ParameterRecord(TypedDict):
    """A helper parameter: its evaluation kind, rename key, and what each site passes."""

    name: str
    kind: ParameterKind
    rename_key: str
    bindings: List[BindingRecord]


HelperScopeKind = Literal["module", "class", "function"]
"""Where a helper was placed; the inventory's ``scope`` is this, then ``:<name>`` unless module.

A class's name is its dotted qualname within the module.
"""


class HelperRecord(TypedDict):
    """Everything a naming assistant is given about one generated helper."""

    file: str
    line: int
    name: str
    scope: str
    rename_key: str
    renameable: bool
    parameters: List[ParameterRecord]
    source: str
    calls: List[CallRecord]
    changes: List[ChangeRecord]


class MappingFormat(TypedDict):
    """How rename keys are spelled, for the assistant that writes the rename file."""

    helper: str
    parameter: str
    notes: str


class HelperInventory(TypedDict):
    """The JSON document ``rename-helpers --list --json`` prints."""

    target: str
    mapping_format: MappingFormat
    helpers: List[HelperRecord]


def _change_sidecar_path(target: "Path") -> "Path":
    """Where the before/after record lives for a dry output target (file or dir)."""
    target = Path(target)
    if target.is_dir():
        return target / CHANGE_SIDECAR_NAME
    return target.with_name(target.name + CHANGE_SIDECAR_NAME)


def _write_change_sidecar(engine: "UnificationRefactorEngine", output: str) -> None:
    """Persist each applied extraction's original block and generated call.

    Grouped by helper name so the rename-helpers inventory can show a
    before/after per call site, which helps a naming assistant finish names,
    docstrings, and types. Written next to the refactored output; delete it
    once naming is done.
    """

    records = engine.change_log
    if not records:
        return
    out = Path(output)
    base = out if out.is_dir() else out.parent
    helpers: Dict[str, List[ChangeRecord]] = {}
    for record in records:
        try:
            rel = os.path.relpath(record.path, str(base))
        except ValueError:
            rel = record.path
        helpers.setdefault(record.helper, []).append(
            {"file": rel, "line": record.line, "before": record.before, "after": record.after}
        )
    sidecar = _change_sidecar_path(out)
    if sidecar.is_symlink():
        raise ValueError(f"Refusing to write the change sidecar through a symlink: {sidecar}")
    _write_atomically(sidecar, json.dumps({"version": 1, "helpers": helpers}, indent=2) + "\n")
    print(f"\nWrote call-site before/after to {sidecar} (for naming; safe to delete).")


def _write_atomically(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` all at once: a failed write leaves the old file or none."""
    descriptor, name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _pending_journal(target: Path, excluded: Sequence[str] = ()) -> Optional[Path]:
    """A journal an interrupted run left that may name a file a run on ``target`` changes.

    Those files are ``target`` itself or the sources it holds. A journal
    names files relative to the directory it sits in, and one whose manifest
    cannot be read names everything beneath it (``journals_covering``); any
    other, under ``target`` or not, concerns nothing the run changes.
    """
    files = [target] if target.is_file() else python_sources(target, excluded=excluded)
    journals = journals_covering({path.resolve() for path in files})
    return journals[0] if journals else None


_COST_NOTE_FILES = 25
"""Below this many files the note is noise: there are few pairs, so few proposals."""


def _import_breadth(files: "Sequence[Path]", root: "Path") -> int:
    """How many third-party packages the project imports.

    A check costs what the import graph costs, not what the file count does:
    about 0.02 s over a hundred modules that import nothing, against about a
    second over Sphinx, whose 243 modules pull in eighteen third-party
    packages and their stubs. Every check re-reads that graph, so its breadth
    is the honest thing to report.
    """
    local = {path.stem for path in files} | {path.parent.name for path in files} | {root.name}
    imported: set[str] = set()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError, ValueError):
            continue  # Only for a message; analysis reports a file it cannot read.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                imported.add(node.module.split(".")[0])
    outside = imported - local - sys.stdlib_module_names - {"__future__"}
    return len(outside)


def _verification_cost(project_path: "Path", types: bool) -> List[str]:
    """What a typed run over this project involves, in facts rather than a forecast.

    How long a run takes is governed by how many proposals the project rejects,
    and that is not known until it has rejected them: on Sphinx the rejections
    outnumbered the refactorings about twenty-five to one late in the run. A
    figure derived from anything available here would be confident and wrong,
    so this states what is known and leaves the judgement to the reader.
    """
    from towel.type_inference import _configured_root

    if not types or not project_path.is_dir():
        return []
    try:
        files = list(project_path.rglob("*.py"))
    except OSError:
        # Only for a message; a real I/O problem resurfaces in the analysis.
        return []
    if len(files) < _COST_NOTE_FILES:
        return []
    packages = _import_breadth(files, project_path)
    checkers = [
        name for name in ("mypy", "pyright") if _configured_root(project_path, name) is not None
    ]
    named = " and ".join(checkers) if checkers else "the installed checker"
    return [
        f"Type checking is on ({named}), and this is where the time goes. Every",
        "candidate signature is verified against the whole project: " f"{len(files)} Python files",
        f"here, importing {packages} third-party packages, and a proposal can take several",
        "such checks. A check costs what that import graph costs, not what the file",
        "count does, and how many checks a run needs depends on how many proposals the",
        "project rejects, which is not known in advance. For scale: Sphinx, 243 files",
        "over 18 such packages, checks in about a second, and a full fixed point there",
        "ran over an hour. --max-refactorings N stops after N applied; --no-types skips",
        "verification altogether.",
        "",
    ]


def _type_oracle(project_path: "Path") -> Optional["TypeOracle"]:
    """The checker the project configures (mypy, pyright, or both), or None with a note.

    A configured checker that is not installed is refused by the selection
    itself (``CheckerNotInstalled``), before anything is written.
    """
    from towel.type_inference import type_oracle_for_project

    choice = type_oracle_for_project(project_path)
    if choice.tool is None:
        print(
            f"Note: {choice.note}, so helper annotations are copied from the call sites but not "
            'inferred or verified. Install the types extra (pip install "code-towel[types]").'
        )
    return choice.tool


def _generated_code_formatter(project_path: "Path") -> Optional[Callable[[str], str]]:
    """The formatter the project's configuration calls for, or None with a note."""
    from towel.formatting import formatter_for_project

    choice = formatter_for_project(project_path)
    if choice.tool is None:
        print(
            f"Note: {choice.note}, so generated code is inserted unformatted. "
            'Install the format extra (pip install "code-towel[format]") to format it.'
        )
    elif "not installed" in choice.note:
        print(f"Note: {choice.note}; formatting generated code with {choice.note.split(';')[0]}.")
    return choice.tool


def _import_sorter(project_path: "Path") -> Optional[Callable[[str, str], str]]:
    """Import sorting the project configures, or None (with a note if the tool is missing)."""
    from towel.formatting import import_sorter_for_project

    choice = import_sorter_for_project(project_path)
    if choice.tool is None and choice.note:
        print(f"Note: {choice.note}; inserted imports are left where Towel put them.")
    return choice.tool


def _banner(title: str) -> None:
    """Print a step heading the way every command does."""
    print(title)
    print("=" * 70)
    print()


def _existing_target(path: str) -> Tuple[bool, bool]:
    """``(is_file, is_dir)`` for a path the command may work on; exits with a message otherwise."""

    if not os.path.exists(path):
        print(f"Error: '{path}' does not exist")
        sys.exit(1)
    is_file, is_dir = os.path.isfile(path), os.path.isdir(path)
    if not is_file and not is_dir:
        print(f"Error: '{path}' is neither a file nor a directory")
        sys.exit(1)
    return is_file, is_dir


@dataclass(frozen=True)
class DryOptions:
    """What ``towel dry`` was asked to do, read once from the parsed arguments."""

    input: str
    output: str
    interactive: bool
    max_refactorings: int
    min_lines: int
    max_parameters: int
    parameterize_builtins: bool
    max_pairs: int
    progress: ProgressMode
    types: bool
    format: bool
    exclude: Tuple[str, ...]
    cross_module: bool = False

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "DryOptions":
        return cls(
            input=str(args.input),
            output=str(args.output),
            interactive=bool(args.interactive),
            max_refactorings=int(args.max_refactorings),
            min_lines=int(args.min_lines),
            max_parameters=int(args.max_parameters),
            parameterize_builtins=bool(args.parameterize_builtins),
            max_pairs=int(args.max_pairs),
            progress=normalize_progress(args.progress),
            types=bool(args.types),
            format=bool(args.format),
            exclude=tuple(args.exclude or ()),
            cross_module=bool(args.cross_module),
        )


@dataclass(frozen=True)
class PreviewOptions:
    """What ``towel preview`` was asked to do."""

    target: str
    min_lines: int
    max_parameters: int
    parameterize_builtins: bool
    max_pairs: int
    progress: ProgressMode
    exclude: Tuple[str, ...] = ()
    cross_module: bool = False

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "PreviewOptions":
        return cls(
            target=str(args.target),
            min_lines=int(args.min_lines),
            max_parameters=int(args.max_parameters),
            parameterize_builtins=bool(args.parameterize_builtins),
            max_pairs=int(args.max_pairs),
            progress=normalize_progress(args.progress),
            exclude=tuple(args.exclude or ()),
            cross_module=bool(args.cross_module),
        )


@dataclass(frozen=True)
class RenameOptions:
    """What ``towel rename-helpers`` was asked to do."""

    target: Path
    files: Optional[List[str]]
    functions: Optional[List[str]]
    rename_file: Optional[Path]
    list: bool
    json: bool
    preview: bool
    llm: str

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "RenameOptions":
        return cls(
            target=Path(args.target),
            files=list(args.files) if args.files else None,
            functions=list(args.functions) if args.functions else None,
            rename_file=Path(args.rename_file) if args.rename_file else None,
            list=bool(args.list),
            json=bool(args.json),
            preview=bool(args.preview),
            llm=str(args.llm),
        )


def _run_dry(args: argparse.Namespace) -> None:
    """Run the dry command."""
    options = DryOptions.from_namespace(args)
    _warn_about_retired_flags(args)
    # Import here to avoid loading heavy modules if not needed
    from towel.unification.refactor_engine import UnificationRefactorEngine

    input_path = options.input
    output_path = options.output

    is_file, is_dir = _existing_target(input_path)

    if is_file and not input_path.endswith(".py"):
        print(f"Warning: '{input_path}' is not a Python file (.py)")
        if not _confirm("Analyze anyway? (y/N): "):
            return

    # Resolve aliases before checking containment or creating any output.
    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    if source != destination:
        if source in destination.parents or destination in source.parents:
            raise ValueError("Input and output must not contain one another")
        if destination.exists() or Path(output_path).is_symlink():
            raise ValueError(
                "Output already exists; choose a new path or explicitly refactor in place"
            )
    refuse_unparsed_program(source, options.exclude)
    if options.cross_module and is_dir:
        _judge_import_problems(source, options.exclude)

    print("=" * 70)
    _banner("APPLYING REFACTORINGS (FIXED-POINT ITERATION)")
    print("This will apply refactorings one at a time until no more are found.")
    print("Helpers go before the first definition of their module,")
    print("or inside the class or function the duplicates share.")
    print()

    if options.interactive:
        for line in _verification_cost(Path(input_path), options.types):
            print(line)
        if not _confirm("Proceed? (y/N): "):
            print("Aborted.")
            return

    if source == destination:
        journal = _pending_journal(destination, options.exclude)
        if journal is not None:
            raise ValueError(
                "Refusing to refactor in place: "
                + pending_journal_remedy(journal, "this run would change")
            )

    oracle = _type_oracle(Path(input_path)) if options.types else None
    try:
        engine = UnificationRefactorEngine(
            max_parameters=options.max_parameters,
            min_lines=options.min_lines,
            parameterize_builtins=options.parameterize_builtins,
            max_candidate_pairs=options.max_pairs,
            settings=_settings(),
            parameterize_constants=True,
            excluded_directories=options.exclude,
            cross_module_helpers=options.cross_module,
            snippet_formatter=(
                _generated_code_formatter(Path(input_path)) if options.format else None
            ),
            file_finisher=(_import_sorter(Path(input_path)) if options.format else None),
            annotate_helpers=options.types,
            type_oracle=oracle,
        )

        print()

        if is_file:
            print(f"Refactoring file: {output_path}")
            final_code, num_applied, descriptions = engine.refactor_to_fixed_point(
                input_path,
                max_iterations=options.max_refactorings,
                progress=options.progress,
                output_path=output_path,
            )
            applied = num_applied

            if num_applied > 0:
                print(f"\nApplied {num_applied} refactoring(s):")
                for i, desc in enumerate(descriptions, 1):
                    print(f"  {i}. {desc}")
            else:
                print("\nNo refactorings found!")
        else:
            print(f"Refactoring directory: {output_path}")
            results, termination_reason = engine.refactor_directory_to_fixed_point(
                input_path,
                output_path,
                max_iterations=options.max_refactorings,
                progress=options.progress,
            )
            applied = sum(count for count, _ in results.values())

            if results:
                total_refactorings = applied
                print(
                    f"\nApplied {total_refactorings} refactoring(s) across {len(results)} file(s)"
                )
                print(f"  Termination: {termination_reason}")
                for file_path, (count, descriptions) in sorted(results.items()):
                    print(f"\n  {file_path}: {count} refactoring(s)")
                    for desc in descriptions[:3]:
                        print(f"    - {desc}")
                    if len(descriptions) > 3:
                        print(f"    ... and {len(descriptions) - 3} more")
            else:
                print(f"\nNo refactorings found! Termination: {termination_reason}")

        if engine.checker_failures:
            # Applied refactorings were verified; these were not judged at all.
            print(
                f"\n{engine.checker_failures} proposal(s) were dropped because the type checker"
                " could not run for them (see the warnings above); they were not judged."
            )
        _print_declined(engine.run_report, applied)
        _write_change_sidecar(engine, output_path)
    finally:
        if oracle is not None:
            oracle.close()


STRAY_COPY_REMEDY = (
    "For a stray copy, or an import that names a module two ways or climbs out of its package:"
    " leave out the directory holding it with --exclude <directory name> (for example"
    " --exclude build), or fix the import."
)
"""What a user can do about a name the project's own tree leaves in doubt."""

INSTALLED_COPY_REMEDY = (
    "For a copy installed outside the project, which --exclude cannot reach: run Towel from an"
    " environment where that name is this tree, such as one with the project installed editable"
    " (pip install -e .), or from one without it; or, if the project's own directory of that"
    " name is not what the program imports, leave it out with --exclude <directory name>."
)
"""What a user can do about a name an installed copy leaves in doubt."""

REQUIRED_DISTRIBUTION_REMEDY = (
    "For a distribution the project requires, which is what the installed project imports by"
    " that name: rename the project's directory of that name, or leave it out with --exclude"
    " <directory name>; if that directory is what the program means, drop the requirement."
)
"""What a user can do about a name a distribution the project requires leaves in doubt."""

MISSING_MODULE_OUTSIDE_REMEDY = (
    "Fix the import, or leave its directory out with --exclude <directory name>."
)
"""What a user can do about a file importing a module the tree lacks from outside its package."""

MISSING_MODULE_INSIDE_REMEDY = (
    "A module generated at build time, such as a _version.py, appears once the project is"
    " installed (pip install -e .); fix any other."
)
"""What a user can do about a package importing a module of its own the tree lacks."""


def _judge_import_problems(target: Path, excluded: Sequence[str]) -> None:
    """Report what keeps the program's imports from naming its modules; refuse when the target's own.

    A helper shared across modules is imported by the name the program's own
    imports give its host (docs/DECISIONS.md, "Import names come from the
    program"). So every problem is named before anything is written. One that
    leaves a name of the package being refactored in doubt refuses the run
    (:func:`_problems_involving`): Towel cannot tell which reading of the
    program is the real one, and the helpers shared across its modules are
    what ``--cross-module`` asks for. Any other is reported, and the run goes
    on. An import of a module the tree lacks leaves no name in doubt,
    wherever it lies; the run leaves the file making it unchanged and says so
    (:func:`_missing_module_report`). Only a run with ``--cross-module`` asks:
    without it no import that runs is written, so nothing depends on them.
    """
    from towel.consumers import ScanLimitExceeded
    from towel.import_model import RelativeImportMissing, UnresolvedImport, build_import_model
    from towel.project_layout import find_project_root
    from towel.unification.exceptions import AmbiguousImportsError, ProjectScanLimitError

    root = find_project_root(target)
    try:
        model = build_import_model(root, excluded_names=excluded)
    except ScanLimitExceeded as error:
        raise ProjectScanLimitError(
            f"{error}. Towel reads the project from the nearest directory with a"
            " pyproject.toml, setup.cfg or setup.py; give the code one, or move it out of the"
            " larger tree"
        ) from error
    if not model.problems:
        return
    involved = _problems_involving(model, target)
    if involved:
        others = len(model.problems) - len(involved)
        raise AmbiguousImportsError(
            f"Refusing to share helpers across the modules of {target}: the program's imports"
            " do not name them unambiguously, so no import of one could be shown to work:\n"
            + "".join(f"  {problem.describe(model.root)}\n" for problem in involved)
            + (f"({others} other problem(s) alone would not stop the run.)\n" if others else "")
            + _import_problem_remedies(involved)
        )
    missing = [
        problem
        for problem in model.problems
        if isinstance(problem, (UnresolvedImport, RelativeImportMissing))
    ]
    in_doubt = [problem for problem in model.problems if problem not in missing]
    if in_doubt:
        LOG.warning(
            "The program's imports do not name every module unambiguously, so no helper is shared"
            " across the modules these involve:\n%s%s",
            "".join(f"  {problem.describe(model.root)}\n" for problem in in_doubt),
            _import_problem_remedies(in_doubt),
        )
    if missing:
        LOG.warning("%s", _missing_module_report(model, missing, target))


def _import_problem_remedies(problems: Sequence["ImportProblem"]) -> str:
    """The remedy for each kind of doubt ``problems`` raise, one line each, in a fixed order.

    ``--exclude`` sets aside a stray copy in the tree, but no installed copy,
    and a directory named like a distribution the project requires is
    resolved only by telling the two apart.
    """
    from towel.import_model import Doubt

    remedies = {
        Doubt.TREE: STRAY_COPY_REMEDY,
        Doubt.INSTALLED: INSTALLED_COPY_REMEDY,
        Doubt.REQUIRED: REQUIRED_DISTRIBUTION_REMEDY,
    }
    present = {doubt for problem in problems for doubt in problem.doubts}
    return "\n".join(remedies[doubt] for doubt in Doubt if doubt in present)


def _missing_module_report(
    model: "ImportModel", problems: Sequence["MissingModule"], target: Path
) -> str:
    """Each file importing a module the tree lacks, left unchanged, with the remedy that fits it.

    A file outside the package whose module it names, test data or an
    example, may be left out with ``--exclude``. One inside, a package's
    import of its own generated ``_version.py``, needs the project
    installed; its directory is the package, and leaving it out is no
    remedy. When the file is a package's initializer, which importing any
    module below it runs, those modules host a helper only for modules
    imported through that package, and a line says so, naming the file.
    """
    root = model.root
    resolved = target.resolve()
    files = sorted({problem.site.file for problem in problems})

    def inside(path: Path) -> bool:
        return path.name == "__init__.py" or all(
            problem.from_inside for problem in problems if problem.site.file == path
        )

    def shown(paths: Iterable[Path]) -> str:
        return ", ".join(str(path.relative_to(root)) for path in paths)

    lines = [
        "These files import a module the tree lacks; the run leaves each one unchanged and goes on:",
        *(f"  {problem.describe(root)}" for problem in problems),
    ]
    outside = [path for path in files if not inside(path)]
    if outside:
        lines.append(f"Left unchanged: {shown(outside)}. {MISSING_MODULE_OUTSIDE_REMEDY}")
    within = [path for path in files if inside(path)]
    if within:
        lines.append(f"Left unchanged: {shown(within)}. {MISSING_MODULE_INSIDE_REMEDY}")
    for path in files:
        package = path.parent
        if path.name == "__init__.py" and (
            package.is_relative_to(resolved) or resolved.is_relative_to(package)
        ):
            lines.append(
                f"Modules in {shown([package])} host a helper only for modules imported through"
                f" it: importing one from anywhere else would run {shown([path])}, whose import"
                " the tree lacks, where it has not run."
            )
    return "\n".join(lines)


def _problems_involving(model: "ImportModel", target: Path) -> List["ImportProblem"]:
    """The problems that leave a name of the package being refactored in doubt.

    One does when it leaves in doubt a top-level name one of whose locations
    lies under ``target`` or holds it, or when it lies under ``target`` and
    leaves in doubt the name of the file it lies in, as a relative import
    climbing out of a package no import names does. anyio's stale
    ``build/lib/anyio`` beside ``anyio`` is a problem of ``anyio``, wherever
    the stray copy sits. An import of a module the tree lacks
    (:data:`~towel.import_model.MissingModule`) leaves no name in doubt,
    wherever it lies: sphinx's test data importing ``sphinx.missing_module4``
    stops no run, from the root or on ``sphinx``, and neither does a
    package's import of the ``_version.py`` its build generates.
    """
    from towel.import_model import RelativeImportMissing, UnresolvedImport

    resolved = target.resolve()

    def under(path: Path) -> bool:
        return path == resolved or path.is_relative_to(resolved)

    own = {
        name
        for name, info in model.names.items()
        if any(under(location) or resolved.is_relative_to(location) for location in info.candidates)
    }
    return [
        problem
        for problem in model.problems
        if not isinstance(problem, (UnresolvedImport, RelativeImportMissing))
        and (problem.names_in_doubt & own or any(under(path) for path in problem.found_at))
    ]


def _print_declined(report: "RunReport", applied: int) -> None:
    """Say what the run declined, and why, so "No refactorings found!" is never the whole story.

    Proposals that were built and then not applied are always counted. The
    candidate pairs the analysis declined are counted when nothing was
    applied, which is when a reader asks why.
    """
    from towel.unification.fixed_point import counted_reasons

    lines: List[str] = []
    if report.declined_proposals:
        lines.append(
            f"  {sum(report.declined_proposals.values())} proposal(s) not applied: "
            f"{counted_reasons(report.declined_proposals)}"
        )
    if not applied and report.declined_pairs:
        lines.append(
            f"  {sum(report.declined_pairs.values())} candidate pair(s) declined: "
            f"{counted_reasons(report.declined_pairs)}"
        )
    if lines:
        print("\nDeclined (DEBUG_PROPOSAL_REJECTIONS=1 traces each candidate pair):")
        for line in lines:
            print(line)


def _print_proposal(
    index: int,
    prop: "RefactoringProposal",
    target: str,
    is_dir: bool,
    source_cache: Dict[str, List[str]],
) -> None:
    """Print one proposal: its scope, the helper it adds or the function it reuses, its call sites."""
    print(f"\n{index}. {prop.description}")
    print(f"   Parameters: {prop.parameters_count}")
    files_affected = {replacement.file_path or prop.file_path for replacement in prop.replacements}
    if len(files_affected) > 1:
        print(f"   Type: Cross-file ({len(files_affected)} files)")
        for f in sorted(files_affected):
            print(f"      - {f}")
    else:
        print(f"   Type: Same file ({prop.file_path})")
    if prop.reused_function is not None:
        print(
            f"\n   Calls existing function {prop.reused_function.name} "
            f"({prop.reused_function.file_path}); no helper is added"
        )
    else:
        print("\n   Extracted function preview:")
        try:
            lines = _helper_preview(prop).split("\n")
            for line in lines[:8]:
                print(f"      {line}")
            if len(lines) > 8:
                print(f"      ... ({len(lines) - 8} more lines)")
        except ValueError as e:
            print(f"      (Preview unavailable: {e})")
            print(f"      Function name: {prop.extracted_function.name}")
    _print_call_sites(prop, target, is_dir, source_cache)


def _helper_preview(prop: "RefactoringProposal") -> str:
    """The helper as it will be written, with the comments it carries, before formatting."""
    from towel.unification.block_comments import CommentPlacementError, weave_comments

    if not prop.helper_comments.comments:
        return ast.unparse(prop.extracted_function)
    try:
        return weave_comments(
            prop.extracted_function, prop.extracted_function, prop.helper_comments
        ).text
    except CommentPlacementError:
        return ast.unparse(prop.extracted_function)


def _print_call_sites(
    prop: "RefactoringProposal", target: str, is_dir: bool, source_cache: Dict[str, List[str]]
) -> None:
    """Print each call site's original block (before) and the generated call (after)."""
    print("\n   Call sites (- before / + after):")
    shown = 0
    for repl in sorted(
        prop.replacements, key=lambda r: ((r.file_path or prop.file_path), r.line_range[0])
    ):
        if shown >= 3:
            print(f"      ... ({len(prop.replacements) - shown} more call site(s))")
            break
        fpath = repl.file_path or prop.file_path
        start, end = repl.line_range
        if fpath not in source_cache:
            try:
                source_cache[fpath] = source_lines(read_source(fpath))
            except (OSError, UnicodeError, SyntaxError):
                source_cache[fpath] = []
        file_lines = source_cache[fpath]
        if not (1 <= start <= end <= len(file_lines)):
            continue
        before = textwrap.dedent("".join(file_lines[start - 1 : end])).rstrip("\n")
        try:
            after = ast.unparse(repl.node)
        except ValueError:
            continue
        location = os.path.relpath(fpath, target) if is_dir else os.path.basename(fpath)
        print(f"      {location}:{start}")
        for line in before.split("\n"):
            print(f"        - {line}")
        for line in after.split("\n"):
            print(f"        + {line}")
        shown += 1


def _run_preview(args: argparse.Namespace) -> None:
    """Run the preview command."""
    options = PreviewOptions.from_namespace(args)
    _warn_about_retired_flags(args)
    from towel.unification.refactor_engine import UnificationRefactorEngine
    from towel.unification.overlap import filter_overlapping_proposals

    target = options.target

    is_file, is_dir = _existing_target(target)
    journal = _pending_journal(Path(target), options.exclude)
    if journal is not None:
        LOG.warning("%s", pending_journal_remedy(journal, "a run here would change"))
    refuse_unparsed_program(Path(target), options.exclude)
    if options.cross_module and is_dir:
        _judge_import_problems(Path(target).resolve(), options.exclude)

    engine = UnificationRefactorEngine(
        max_parameters=options.max_parameters,
        min_lines=options.min_lines,
        parameterize_builtins=options.parameterize_builtins,
        max_candidate_pairs=options.max_pairs,
        settings=_settings(),
        parameterize_constants=True,
        excluded_directories=options.exclude,
        cross_module_helpers=options.cross_module,
    )

    # Analyze
    if is_file:
        if not target.endswith(".py"):
            print(f"Note: '{target}' is not a Python file (.py)")
            print()

        print(f"Analyzing file: {target}")
        all_proposals = engine.analyze_files([target], progress=options.progress)
    else:
        print(f"Analyzing directory: {target}")
        all_proposals = engine.analyze_directory(target, recursive=True, progress=options.progress)

    print(f"\nFound {len(all_proposals)} refactoring opportunities")

    if not all_proposals:
        print("No duplicates found!")
        return

    proposals = filter_overlapping_proposals(all_proposals)

    if len(proposals) < len(all_proposals):
        print(f"Filtered to {len(proposals)} non-overlapping proposals")
        print(f"(Removed {len(all_proposals) - len(proposals)} overlapping proposals)")

    print("\n" + "=" * 70)
    print("REFACTORING OPPORTUNITIES")
    print("=" * 70)

    source_cache: Dict[str, List[str]] = {}
    for i, prop in enumerate(proposals[:10], 1):
        _print_proposal(i, prop, target, is_dir, source_cache)

    if len(proposals) > 10:
        print(f"\n... and {len(proposals) - 10} more proposals")

    print("\n" + "=" * 70)
    print("\nTo apply these refactorings, run:")
    if is_file:
        print(f"  towel dry {target} <output>")
    else:
        print(f"  towel dry {target} <output_dir>")
    print()


def _run_rename_helpers(args: argparse.Namespace) -> None:
    """Run the rename-helpers command."""
    options = RenameOptions.from_namespace(args)
    target = options.target

    if not target.exists():
        print(f"Error: '{target}' does not exist")
        sys.exit(1)

    if not target.is_dir():
        print(f"Error: '{target}' must be a directory")
        sys.exit(1)
    journal = _pending_journal(target)
    if journal is not None:
        LOG.warning("%s", pending_journal_remedy(journal, "a rename here would change"))

    helpers = _find_extracted_helpers(
        target,
        options.files,
        options.functions,
        include_named=bool(options.rename_file and not options.list),
    )

    if not helpers and not options.rename_file:
        # A mapping may still rename parameters of helpers renamed earlier;
        # the planner validates every key against the current source.
        print(f"No extracted helper functions found in {target}")
        return

    # List mode
    if options.list and options.json:
        print(json.dumps(helper_inventory(target, helpers), indent=2))
        return
    if options.list:
        print(f"\nFound {len(helpers)} extracted helper function(s):\n")
        for file_path, func_name, lineno, source_preview in helpers:
            rel_path = file_path.relative_to(target)
            print(f"  {rel_path}:{lineno}")
            print(f"    Function: {func_name}")
            if source_preview:
                lines = source_preview.split("\n")[:3]
                for line in lines:
                    print(f"      {line}")
                if len(source_preview.split("\n")) > 3:
                    print("      ...")
            print()
        return

    if options.rename_file:
        _apply_rename_file(
            target,
            helpers,
            options.rename_file,
            options.preview,
            options.json,
            restrict_selection=bool(options.files or options.functions),
        )
        return

    # Interactive LLM mode
    _run_interactive_llm_mode(target, helpers, options.llm, options.preview)


def _find_extracted_helpers(
    target: Path,
    file_filters: Optional[List[str]],
    function_filters: Optional[List[str]],
    *,
    include_named: bool = False,
) -> List[Tuple[Path, str, int, str]]:
    """Find selected helpers, including previously named ones in mapping-file mode.

    Inventory and interactive suggestions use generated names only. A mapping
    may instead name a helper whose function was already renamed, so filters
    must also recognize its current name when selecting a parameter mapping.
    File filters name exact paths relative to the requested target.
    """
    helpers = []
    selected_files = {(target / name).resolve() for name in file_filters or ()}

    for py_file, (source, tree) in _load_modules(target).items():
        if selected_files and py_file.resolve() not in selected_files:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not include_named and not GENERATED_HELPER_NAME.fullmatch(node.name):
                continue
            if function_filters and node.name not in function_filters:
                continue
            lines = source.split("\n")
            start = node.lineno - 1
            preview = "\n".join(lines[start : start + 5])
            helpers.append((py_file, node.name, node.lineno, preview))

    return sorted(helpers, key=lambda x: (str(x[0]), x[2]))


def _load_modules(target: Path) -> Dict[Path, Tuple[str, ast.Module]]:
    """Every parseable module under ``target`` with its source, in path order.

    Symlinks are left out, as directory refactoring leaves them out, and a
    file that does not decode or parse is skipped: one policy for every
    command that reads a refactored tree.
    """
    modules: Dict[Path, Tuple[str, ast.Module]] = {}
    for path in python_sources(target):
        try:
            source = read_source(path)
            modules[path] = (source, ast.parse(source))
        except (OSError, SyntaxError, UnicodeError):
            continue
    return modules


def _confirm(prompt: str) -> bool:
    """Ask a yes/no question; a closed stdin or an interrupt answers no."""
    try:
        return input(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt, OSError, RuntimeError):
        # A closed or lost stdin answers no; ``input`` raises RuntimeError for
        # a closed descriptor and OSError for one that cannot be read.
        print()
        return False


def _change_record(value: object) -> Optional[ChangeRecord]:
    """``value`` as a change record when it has exactly the sidecar's shape."""
    if not isinstance(value, dict):
        return None
    file, line, before, after = (value.get(key) for key in ("file", "line", "before", "after"))
    if (
        isinstance(file, str)
        and isinstance(line, int)
        and isinstance(before, str)
        and isinstance(after, str)
    ):
        return ChangeRecord(file=file, line=line, before=before, after=after)
    return None


def _read_change_sidecar(sidecar: Path) -> Dict[str, List[ChangeRecord]]:
    """The call-site changes a ``dry`` run recorded, by helper; empty when absent or malformed.

    The sidecar is Towel's own output, so a record of the wrong shape means
    the file was edited or written by another version; it is reported and
    the inventory proceeds without change records.
    """
    if not sidecar.is_file():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    helpers = data.get("helpers") if isinstance(data, dict) else None
    if not isinstance(helpers, dict):
        return {}
    changes: Dict[str, List[ChangeRecord]] = {}
    for helper, records in helpers.items():
        parsed = [
            _change_record(record) for record in (records if isinstance(records, list) else [])
        ]
        if not isinstance(helper, str) or any(record is None for record in parsed):
            LOG.warning("Ignoring malformed change records in %s", sidecar)
            return {}
        changes[helper] = [record for record in parsed if record is not None]
    return changes


def _helper_calls(
    modules: Dict[Path, Tuple[str, ast.Module]], wanted: Set[str], target: Path
) -> Dict[str, List[CallRecord]]:
    """Every call of a wanted helper, by helper name, with the statement and arguments it stands in."""
    calls: Dict[str, List[CallRecord]] = {name: [] for name in wanted}
    for path, (source, tree) in modules.items():
        statements = {
            child: statement
            for statement in ast.walk(tree)
            if isinstance(statement, ast.stmt)
            for child in ast.walk(statement)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.id
                if isinstance(function, ast.Name)
                else (function.attr if isinstance(function, ast.Attribute) else None)
            )
            if name not in wanted:
                continue
            statement = statements.get(node, node)
            calls[name].append(
                {
                    "file": str(path.relative_to(target)),
                    "line": node.lineno,
                    "bound": isinstance(function, ast.Attribute),
                    "statement": ast.get_source_segment(source, statement) or "",
                    "arguments": [ast.get_source_segment(source, arg) or "" for arg in node.args],
                }
            )
    return calls


def _private_helper_calls(
    source: str, tree: ast.Module, owner: ast.ClassDef, name: str, relative: str
) -> List[CallRecord]:
    """Every call of a class-private helper: an attribute call in its own class's body.

    ``obj.__h()`` written anywhere else names another stored attribute, so a
    same-named helper of another class is not counted as a call of this one.
    """
    owners = mangling_classes(tree)
    statements = {
        child: statement
        for statement in ast.walk(tree)
        if isinstance(statement, ast.stmt)
        for child in ast.walk(statement)
    }
    return [
        {
            "file": relative,
            "line": node.lineno,
            "bound": True,
            "statement": ast.get_source_segment(source, statements.get(node, node)) or "",
            "arguments": [ast.get_source_segment(source, arg) or "" for arg in node.args],
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and owners.get(node) is owner
    ]


def helper_inventory(target: Path, helpers: List[Tuple[Path, str, int, str]]) -> HelperInventory:
    """Describe every generated helper for a naming assistant.

    Each entry gives the helper's scope, source, parameters with their
    evaluation kind (``thunk`` parameters are called as ``name()`` inside the
    helper, ``lifted`` ones are called with block variables, ``receiver`` is
    the bound instance or class), every call site with the argument
    expression bound to each parameter, and the exact mapping keys that
    rename the helper or one of its parameters. A class-private helper is
    keyed by its class, ``path.py:Class.__helper``, because its name means
    something only there: the class stores it as ``_Class__helper``.
    """

    changes_by_helper = _read_change_sidecar(_change_sidecar_path(target))

    wanted = {name for _, name, _, _ in helpers}
    modules = _load_modules(target)
    calls = _helper_calls(modules, wanted, target)
    entries: List[HelperRecord] = []
    for path, name, lineno, _ in helpers:
        source, tree = modules[path]
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        qualnames = class_qualnames(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != name or node.lineno != lineno:
                continue
            enclosing = parents.get(node)
            scope_kind: HelperScopeKind
            if isinstance(enclosing, ast.ClassDef):
                scope_kind, scope_name = "class", qualnames.get(enclosing, enclosing.name)
            elif isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope_kind, scope_name = "function", enclosing.name
            else:
                scope_kind, scope_name = "module", None
            scope = scope_kind if scope_name is None else f"{scope_kind}:{scope_name}"
            relative = str(path.relative_to(target))
            private = (
                isinstance(enclosing, ast.ClassDef)
                and is_class_private(name)
                and mangling_prefix(enclosing.name) is not None
            )
            helper_calls = (
                _private_helper_calls(source, tree, enclosing, name, relative)
                if private and isinstance(enclosing, ast.ClassDef)
                else calls[name]
            )
            decorators = {
                (d.id if isinstance(d, ast.Name) else getattr(d, "attr", ""))
                for d in node.decorator_list
            }
            names = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
            kinds: Dict[str, ParameterKind] = {}
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    if child.func.id in names:
                        kinds[child.func.id] = "thunk" if not child.args else "lifted"
            has_receiver = scope_kind == "class" and "staticmethod" not in decorators
            if private:
                key = f"{relative}:{scope_name}.{name}"
            elif scope_kind == "module":
                key = f"{relative}:{name}"
            else:
                key = name
            parameters: List[ParameterRecord] = []
            for index, parameter in enumerate(names):
                kind: ParameterKind
                if index == 0 and has_receiver:
                    kind = "receiver"
                else:
                    kind = kinds.get(parameter, "value")
                bindings: List[BindingRecord] = []
                for call in helper_calls:
                    arguments = call["arguments"]
                    offset = index - (1 if has_receiver and call["bound"] else 0)
                    if 0 <= offset < len(arguments):
                        expression = str(arguments[offset])
                        if kind == "thunk" and expression.startswith("lambda:"):
                            expression = expression[len("lambda:") :].strip()
                        bindings.append(
                            {"file": call["file"], "line": call["line"], "expression": expression}
                        )
                parameters.append(
                    {
                        "name": parameter,
                        "kind": kind,
                        "rename_key": (
                            f"{key}.{parameter}" if private else f"{relative}:{name}.{parameter}"
                        ),
                        "bindings": bindings,
                    }
                )
            changes = changes_by_helper.get(name, [])
            entries.append(
                {
                    "file": relative,
                    "line": lineno,
                    "name": name,
                    "scope": scope,
                    "rename_key": key,
                    "renameable": scope_kind == "module"
                    or (scope_kind == "class" and (private or not name.startswith("__"))),
                    "parameters": parameters,
                    "source": ast.get_source_segment(source, node) or "",
                    "calls": helper_calls,
                    "changes": (
                        [change for change in changes if change["file"] == relative]
                        if private
                        else changes
                    ),
                }
            )
    return {
        "target": str(target),
        "mapping_format": {
            "helper": (
                '"path.py:helper", "path.py:Class.__helper" for a class-private helper,'
                ' or "helper" -> new function name'
            ),
            "parameter": (
                '"path.py:helper.parameter", "path.py:Class.__helper.parameter",'
                ' or "helper.parameter" -> new parameter name'
            ),
            "notes": (
                "Thunk parameters are called inside the helper, so name them for the value they yield "
                "(the helper reads name()). A class-private helper (a name starting with '__' in a "
                "class) must get a new name that starts with '__' too, and does not end with '__': "
                "privacy keeps subclasses from overriding it. Keys are applied as one batch; any "
                "invalid entry aborts all."
            ),
        },
        "helpers": entries,
    }


def _apply_rename_file(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    rename_file: Path,
    dry_run: bool,
    as_json: bool = False,
    *,
    restrict_selection: bool = False,
) -> None:
    """Apply renamings from a JSON file, reporting a structured result when asked.

    A planning failure is a normal outcome for an assistant choosing names:
    it exits with status 2 and states the reason, so the caller can pick
    another name and retry. Nothing is written unless every entry is valid.
    """

    # An unreadable or malformed file is an OSError or ValueError the dispatcher reports.
    with open(rename_file, encoding="utf-8") as f:
        renames = json.load(f)

    if not isinstance(renames, dict):
        raise ValueError("Rename file must contain a JSON object (dict)")

    try:
        total_changes = _apply_rename_mappings(
            target,
            renames,
            dry_run,
            quiet=as_json,
            selected_helpers=helpers if restrict_selection else None,
        )
    except ValueError as error:
        if as_json:
            print(json.dumps({"applied": False, "dry_run": dry_run, "error": str(error)}))
        else:
            print(f"Error: {error}")
        sys.exit(2)
    if as_json:
        print(
            json.dumps(
                {
                    "applied": not dry_run,
                    "dry_run": dry_run,
                    "changes": total_changes,
                    "renames": {str(key): value for key, value in renames.items()},
                }
            )
        )
        return
    print(f"\n{'[PREVIEW] Would make' if dry_run else 'Applied'} {total_changes} change(s)")


def _apply_rename_mappings(
    target: Path,
    renames: Mapping[str, object],
    dry_run: bool,
    quiet: bool = False,
    *,
    selected_helpers: Optional[List[Tuple[Path, str, int, str]]] = None,
) -> int:
    """Validate every mapping and stage the entire rename batch before any write."""
    specifications: List[Tuple[str, str, Optional[Path]]] = []
    for spec, new_name in renames.items():
        if not isinstance(new_name, str):
            raise ValueError(f"Invalid rename value for {spec}")
        file_filter = None
        name = spec
        if ":" in spec:
            relative, name = spec.rsplit(":", 1)
            file_filter = (target / relative).resolve()
            if target.resolve() not in file_filter.parents or not file_filter.is_file():
                raise ValueError(
                    f"File-qualified rename must name a file within target: {relative}"
                )
        if selected_helpers is None:
            specifications.append((name, new_name, file_filter))
        else:
            # ``helper``, ``helper.parameter``, or a class-private
            # ``Class.helper[.parameter]``: the helper is the first part that
            # names a selected one.
            selected_names = {helper for _, helper, _, _ in selected_helpers}
            helper_name = next(
                (part for part in name.split(".") if part in selected_names),
                name.partition(".")[0],
            )
            matched = {
                path.resolve()
                for path, helper, _, _ in selected_helpers
                if helper == helper_name and (file_filter is None or path.resolve() == file_filter)
            }
            if not matched:
                raise ValueError(f"Rename is outside the selected helpers: {spec}")
            specifications.extend((name, new_name, path) for path in sorted(matched))
    count = _rename_batch(target, specifications, dry_run)
    if not quiet:
        for spec, new_name in renames.items():
            print(f"  {spec} -> {new_name}")
    return count


def _rename_function_in_directory(
    target: Path,
    old_name: str,
    new_name: str,
    dry_run: bool,
    file_filter: Optional[Path] = None,
) -> int:
    """Rename resolved module helpers using one recoverable batch."""
    return _rename_batch(target, [(old_name, new_name, file_filter)], dry_run)


def _rename_batch(
    target: Path, specifications: List[Tuple[str, str, Optional[Path]]], dry_run: bool
) -> int:
    from towel.renaming import plan_renames

    plan, count = plan_renames(target, specifications)
    if not dry_run:
        apply_changes(plan)
    return count


def _run_interactive_llm_mode(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    llm_type: str,
    dry_run: bool,
) -> None:
    """Run interactive mode: generate LLM prompt and apply suggestions."""
    print(f"\nFound {len(helpers)} extracted helper function(s) to rename.\n")
    print("=" * 70)
    _banner("STEP 1: LLM PROMPT GENERATION")

    prompt = _generate_llm_prompt(target, helpers, llm_type)

    print("Copy the following prompt and paste it into your LLM assistant:")
    print("\n" + "=" * 70)
    print(prompt)
    print("=" * 70 + "\n")

    print("After the LLM provides suggestions, paste the JSON response below.")
    print("The response should be a JSON object mapping old names to new names.")
    print("You can use file-qualified names (e.g., 'path/to/file.py:func_name') to")
    print("rename functions only in specific files, or just the function name to rename")
    print("across all files. A class-private helper, '__...' in a class, is named by its")
    print("class ('path/to/file.py:Class.__func_name') and keeps a name starting with '__'.")
    print()
    print("Example:")
    print('  {"__extracted_func_1": "calculate_total"}')
    print('  {"src/utils.py:__extracted_func_2": "validate_input"}')
    print('  {"src/box.py:Box.__extracted_func_3": "__scaled_width"}')
    print()
    print(
        "Paste the JSON response below, then press ENTER followed by Ctrl+D (or Ctrl+Z on Windows):"
    )
    print()

    try:
        llm_response = sys.stdin.read().strip()
    except KeyboardInterrupt:
        print("\nAborted.")
        return

    if not llm_response:
        print("No response provided. Aborted.")
        return

    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", llm_response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_match = re.search(r"\{.*\}", llm_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = llm_response

    try:
        renames = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"\nError parsing JSON response: {e}")
        print("Please ensure the response is valid JSON.")
        sys.exit(1)

    if not isinstance(renames, dict):
        print("Error: Response must be a JSON object (dict)")
        sys.exit(1)

    print("\n" + "=" * 70)
    _banner("STEP 2: APPLYING RENAMINGS")

    total_changes = _apply_rename_mappings(target, renames, dry_run, selected_helpers=helpers)
    print(f"\n{'[PREVIEW] Would make' if dry_run else 'Applied'} {total_changes} change(s)")


def _generate_llm_prompt(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    llm_type: str,
) -> str:
    """Generate a prompt for an LLM to suggest better function names."""

    intro = """I have extracted duplicate code into helper functions, but they have generic names like __extracted_func_1, __extracted_func_2, etc.

Please review each function and suggest a better, more descriptive name based on what the function does. The new names should:
- Be descriptive and indicate the function's purpose
- Follow Python naming conventions (lowercase with underscores)
- Be concise but meaningful
- Avoid generic names like "helper" or "utility"

Here are the extracted functions:

"""

    keys = {
        (entry["file"], entry["name"], entry["line"]): entry["rename_key"]
        for entry in helper_inventory(target, helpers)["helpers"]
    }
    functions_section = ""
    for i, (file_path, func_name, lineno, source_preview) in enumerate(helpers, 1):
        rel_path = file_path.relative_to(target)
        key = keys.get((str(rel_path), func_name, lineno), func_name)
        functions_section += f"\n{i}. {func_name} ({rel_path}:{lineno}), rename key {key!r}\n"
        if source_preview:
            functions_section += "```python\n"
            functions_section += source_preview + "\n"
            functions_section += "```\n"

    output_format = """

Please provide your suggestions as a JSON object mapping old names to new names.

If the same function name appears in multiple files, use file-qualified names (path:function_name)
to rename them individually. Otherwise, you can use just the function name to rename across all files.
A helper whose rename key names a class ("path.py:Class.__name") is class-private: use that key, and
give it a new name that also starts with two underscores (and does not end with two), such as
"__scaled_width", because the privacy is what keeps subclasses from overriding it.

For example:

```json
{
  "src/utils.py:__extracted_func_1": "calculate_total_price",
  "src/validators.py:__extracted_func_1": "validate_email_format",
  "__extracted_func_2": "format_date_string"
}
```

Only include the JSON object in your response.
"""

    return intro + functions_section + output_format


if __name__ == "__main__":
    main()
