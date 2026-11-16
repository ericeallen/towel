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
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, replace
from gettext import gettext
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess  # nosec B404: runs only git, found on PATH, for a warning
import sys
import tempfile
import textwrap
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import (
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Mapping,
    NoReturn,
    Optional,
    Sequence,
    Set,
    TYPE_CHECKING,
    Tuple,
)

if TYPE_CHECKING:
    from towel.filesystem import StagedProject
    from towel.import_model import ImportModel, ImportProblem, MissingModule
    from towel.type_inference import TypeOracle
    from towel.unification.fixed_point import BeforePublication, RunReport
    from towel.unification.models import RefactoringProposal
    from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.changes import apply_changes, journals_covering, pending_journal_remedy, recover
from towel.execution_budget import BudgetExpired, execution_budget
from towel.cli_reporting import (
    BindingRecord as BindingRecord,
    CallRecord as CallRecord,
    ChangeRecord as ChangeRecord,
    CommandResult as CommandResult,
    mode_name,
    HelperInventory as HelperInventory,
    HelperRecord as HelperRecord,
    HelperScopeKind as HelperScopeKind,
    ParameterRecord as ParameterRecord,
    TypesStatus,
    LARGEST_SHOWN,
    analysed_record,
    census_line,
    left_alone_count,
    left_alone_lines,
    outcome_line,
    reason_lines,
    render_preview,
    shows_pairs,
    run_outcome,
    source_diff,
    stopped_at_limit,
)
from towel.diagnostics import LOG, Settings, configure_stderr_logging
from towel.relaxed_suitability import RelaxedFinding, findings_for_paths, relaxed_notice
from towel.config import ConfigurationError, apply_project_defaults
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
    DEFAULT_STRICT as DEFAULT_STRICT,
)
from towel.unification.progress import (
    DEFAULT_PROGRESS,
    ProgressMode,
    normalize_progress,
    progress_run,
    write_note,
)

_MISSING_OUTPUT = gettext("the following arguments are required: %s") % "output"
"""argparse's message, in its own translation, for a dry command line that names no output."""


class _TowelParser(argparse.ArgumentParser):
    """Towel's parsers: a dry command line without an output is told what to add.

    argparse alone said only "the following arguments are required: output"
    (user audit CLI-09). The input, which that message lacks, is recorded as
    it is parsed (:class:`_DryInput`); every command's parser is of this class.
    """

    dry_input: Optional[str] = None

    def error(self, message: str) -> NoReturn:
        if self.dry_input is not None and message == _MISSING_OUTPUT:
            message = _one_path_dry(self.dry_input)
        super().error(message)


class _DryInput(argparse.Action):
    """Store dry's input path, and record it on the parser for a missing-output message."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: Optional[str] = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        if isinstance(parser, _TowelParser) and isinstance(values, str):
            parser.dry_input = values


def _one_path_dry(given: str) -> str:
    """What to tell ``towel dry <given>``: a new output path, or the same path twice."""
    path = Path(given)
    if path.suffix == ".py":
        kind, output = "file", str(path.with_name(f"{path.stem}_cleaned{path.suffix}"))
    else:
        kind, output = "directory", "cleaned"
    return (
        "dry needs an output path as well as the input.\n"
        f"It writes the refactored code to a new {kind} and leaves {given} unchanged:\n"
        f"  towel dry {shlex.quote(given)} {shlex.quote(output)}\n"
        "To refactor in place instead, give the same path twice:\n"
        f"  towel dry {shlex.quote(given)} {shlex.quote(given)}"
    )


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Descriptions and examples as written; option text wrapped, never inside an option's name.

    textwrap breaks a line after any hyphen, which split ``--no-interactive``
    into ``--no-`` and ``interactive`` (user audit CLI-03).
    """

    def _split_lines(self, text: str, width: int) -> List[str]:
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False)


def _towel_version() -> str:
    """This Towel's version: its distribution's, else its source tree's, else "unknown".

    A source tree that is not installed, run as ``PYTHONPATH=src python -m
    towel``, has no distribution metadata; asking for it once stopped every
    command with PackageNotFoundError while the parser was being built.
    """
    try:
        return version("code-towel")
    except PackageNotFoundError:
        return _source_tree_version(Path(__file__).resolve().parents[2]) or "unknown"


def _source_tree_version(root: Path) -> Optional[str]:
    """The version ``root/pyproject.toml`` declares, if that file is Towel's own."""
    import tomllib

    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    table = project.get("project")
    if not isinstance(table, dict) or table.get("name") != "code-towel":
        return None
    declared = table.get("version")
    return declared if isinstance(declared, str) else None


def _build_parser() -> argparse.ArgumentParser:
    """The command-line parser, shared by ``main`` and the tests."""
    parser = _TowelParser(
        prog="towel",
        allow_abbrev=False,
        description="""\
Towel finds repeated code in Python files, including copies that differ in a
few names or values, and replaces it with calls to new helper functions.""",
        formatter_class=_HelpFormatter,
        epilog="""\
Examples, in a directory holding src/:
  towel preview src/          # Show what would change, changing nothing
  towel dry src/ cleaned/     # Write a refactored copy of src/ to cleaned/
  towel dry src/ src/         # Refactor in place (commit your work first)
  towel rename-helpers src/ --complete-scope    # Name the new helpers

Run towel COMMAND --help for a command's options and examples, and see
docs/QUICKSTART.md for a first run.

Exit status: 0 done, including when there was nothing to change; 1 failed,
refused, or answered "no"; 2 invalid arguments, or a rename refused; 3 preview
--check would change code; 124 time limit reached; 130 interrupted;
143 terminated.""",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"towel {_towel_version()}",
    )

    subparsers = parser.add_subparsers(dest="command", title="commands", metavar="COMMAND")

    _add_preview_parser(subparsers)
    _add_dry_parser(subparsers)
    _add_rename_helpers_parser(subparsers)
    _add_recover_parser(subparsers)
    return parser


def main() -> None:
    """Main entry point with subcommands."""
    _install_signal_handling()
    parser = _build_parser()
    args = parser.parse_args()
    try:
        args = apply_project_defaults(parser, args, sys.argv[1:])
    except ConfigurationError as error:
        parser.error(str(error))
    except (OSError, ValueError) as error:
        _write_failure_result(args, "error", "error", str(error))
        parser.exit(1, f"Error: {error}\n")
    if args.config_path is not None:
        print(f"Using [tool.towel] from {args.config_path}", file=sys.stderr)
    if args.command == "dry" and args.json and args.interactive:
        parser.error("dry --json requires --no-interactive")
    if (
        args.command == "preview"
        and args.quick
        and any((args.json, args.summary, args.diff, args.inventory, args.stats, args.check))
    ):
        parser.error(
            "--quick cannot be combined with --json, --summary, --diff, --inventory, --stats or --check"
        )
    result_stdout = sys.stdout
    configure_stderr_logging()
    _settings().enable_debug_logging()
    if sys.version_info[:2] > (3, 15):
        print(
            "Warning: Towel is validated on Python 3.11–3.15; this interpreter is outside "
            "that range. Review changes and run your project's tests.",
            file=sys.stderr,
        )
    try:
        with (
            progress_run(quiet=bool(getattr(args, "quiet", False))),
            execution_budget(getattr(args, "time_limit", None)),
        ):
            if getattr(args, "quiet", False):
                args.progress = "none"
            _dispatch(parser, args)
    except BudgetExpired:
        # A signal may arrive while redirect_stdout.__enter__ has assigned
        # sys.stdout but before the with statement can register its __exit__.
        # Restore the result channel explicitly before emitting the failure.
        sys.stdout = result_stdout
        _write_failure_result(args, "incomplete", "time_limit", "Time limit exceeded")
        parser.exit(124, "Time limit exceeded; no output was published. Cleanup completed.\n")
    except KeyboardInterrupt:
        sys.stdout = result_stdout
        _write_failure_result(args, "incomplete", "interrupted", "Interrupted")
        parser.exit(130, "Interrupted.\n")
    except BrokenPipeError:
        # The reader went away (``towel preview ... | head``); nothing is wrong.
        _close_stdout_quietly()
        sys.exit(0)


_SETTINGS: Optional[Settings] = None


def _execution_settings(jobs: Optional[int]) -> Settings:
    """An explicit CLI/config worker count wins over the environment."""
    settings = _settings()
    return settings if jobs is None else replace(settings, workers=jobs)


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
        except RunCancelled as cancelled:
            parser.exit(1, f"{cancelled}\n")
        except (OSError, ValueError, TowelError) as error:
            _write_failure_result(args, "error", "error", str(error))
            parser.exit(1, f"Error: {error}\n")
    elif args.command == "preview":
        try:
            _run_preview(args)
        except BrokenPipeError:
            raise
        except (OSError, ValueError, TowelError) as error:
            _write_failure_result(args, "error", "error", str(error))
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
            restored = recover(args.journal)
            noun = "file" if len(restored) == 1 else "files"
            print(f"Restored {len(restored)} {noun}; journal removed.")
        except BrokenPipeError:
            raise
        except (OSError, ValueError) as error:
            parser.exit(1, f"Error: {error}\n")


@dataclass(frozen=True)
class _Count:
    """An argparse type for a count: a whole number that is not negative.

    ``wanted`` follows "is negative;" and says what the option counts, in
    its own words: one sentence for every count once told ``--min-lines -1``
    that "0 runs to a fixed point" (user audit CLI-09).
    """

    wanted: str

    def __call__(self, text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
        if value < 0:
            raise argparse.ArgumentTypeError(f"{value} is negative; {self.wanted}")
        return value


_REFACTORING_COUNT = _Count("give the most refactorings to apply, or 0 for no limit")


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


def _add_exclude_flag(options: argparse._ActionsContainer) -> None:
    """Add ``--exclude``, shared by the commands that analyze a directory."""
    options.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=_excluded_name,
        metavar="NAME",
        help="Leave out every file or directory named NAME, at any depth (repeatable), e.g."
        " --exclude tests: Towel changes nothing in it. Give a name, not a path or a pattern;"
        " by default only hidden directories, __pycache__, node_modules and virtual"
        " environments are left out.",
    )


def _add_cross_module_flag(options: argparse._ActionsContainer) -> None:
    """Add ``--cross-module``, shared by the commands that analyze a project.

    Off by default: a helper shared across modules adds an import between
    them, a change to how the project's modules depend on each other that a
    user should ask for rather than find in the diff.
    """
    options.add_argument(
        "--cross-module",
        dest="cross_module",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Also extract duplicates that span modules: the helper goes in one module and the "
        "others import it (docs/CROSS_MODULE.md explains when this is refused). Default: off: "
        "only duplicates within one module are extracted, so no module starts importing "
        "another.",
    )


def _add_strict_flag(options: argparse._ActionsContainer) -> None:
    """Add ``--strict``, shared by the commands that analyze a project.

    Off by default: relaxed mode assumes a program does not depend on when its
    objects are finalized (docs/design/DECISIONS.md, "Relaxed mode is the default;
    strict mode keeps exact lifetimes").
    """
    options.add_argument(
        "--strict",
        dest="strict",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_STRICT,
        help="Preserve exactly when objects are released and when names are looked up, as "
        "releases before 2.0 did. Expect more blocks left alone and bookkeeping in callers. "
        "Default: off: callers call helpers directly, on the assumption that the program does "
        "not depend on when its objects are released.",
    )


def _add_parameterize_builtins_flag(options: argparse._ActionsContainer) -> None:
    """Add ``--parameterize-builtins``, shared by the commands that analyze a project.

    Off by default: no helper takes a builtin as a parameter, since a call
    such as ``helper(rows, len)`` would surprise its reader.
    """
    options.add_argument(
        "--parameterize-builtins",
        dest="parameterize_builtins",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Also extract duplicates that use a builtin such as len where the copies may not "
        "mean the same len: copies in different modules (with --cross-module), or copies of "
        "which only one has its own variable named len; each call then hands the helper its "
        "own len. Default: off, and such duplicates are left alone; docs/GENERATED_CODE.md "
        "lists the cases left alone even with this option.",
    )


@dataclass(frozen=True)
class _Sections:
    """Where dry and preview list an option in their help, in the order a newcomer reads.

    Add an option to the section that says what it is for: ``output``, what
    the command prints, writes or asks; ``scope``, which code it examines;
    ``limits``, how much it does; ``advanced``, what most runs leave at its
    default. ``output`` is the parser's own options section, retitled, so
    ``--help`` and an option added to the parser itself are listed there.
    Within a section, options are listed in the order they are added.
    """

    output: argparse.ArgumentParser
    scope: argparse._ArgumentGroup
    limits: argparse._ArgumentGroup
    advanced: argparse._ArgumentGroup


def _sections(parser: argparse.ArgumentParser) -> _Sections:
    """Give ``parser`` the four sections of its help, after its positional arguments."""
    parser._optionals.title = "what you get"
    return _Sections(
        output=parser,
        scope=parser.add_argument_group("what is examined"),
        limits=parser.add_argument_group("limits"),
        advanced=parser.add_argument_group("advanced"),
    )


def _add_dry_parser(subparsers: "argparse._SubParsersAction[_TowelParser]") -> None:
    """Add 'dry' subcommand parser."""
    parser = subparsers.add_parser(
        "dry",
        allow_abbrev=False,
        help="Refactor repeated code, into a new directory or in place",
        formatter_class=_HelpFormatter,
        description="""\
Extract each piece of repeated code into a helper function and replace the
copies with calls to it. dry writes the result to output, a new path, and
leaves input unchanged; give the same path twice to refactor in place. It asks
before writing anything, then keeps going until nothing more can be
extracted. Run towel preview first to see what would change.""",
        epilog="""\
Examples, in a directory holding src/:
  # Write a refactored copy of src/ to cleaned/
  towel dry src/ cleaned/

  # Refactor src/ in place (commit your work first, to review the change)
  towel dry src/ src/

  # Unattended: no question, and give up after ten minutes
  towel dry src/ out/ --no-interactive --time-limit 600

When it changes anything, dry also writes .towel-helpers.json into output (or
output.towel-helpers.json beside an output file): each new helper's call
sites, for naming the helpers with rename-helpers. Use --no-helper-metadata
to leave it out; rename-helpers then shows no call-site changes. Delete it
once you no longer need it. TOWEL_WORKERS=N limits the analysis to N worker
processes.""",
    )

    parser.add_argument(
        "input",
        action=_DryInput,
        help="The file or directory to refactor; dry leaves it unchanged unless output "
        "is the same path.",
    )
    parser.add_argument(
        "output",
        help="Where to write the refactored code: a path that does not exist yet, or the same "
        "path as input to refactor in place.",
    )
    sections = _sections(parser)
    sections.output.add_argument(
        "--interactive",
        dest="interactive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ask for confirmation before writing anything (default: on); --no-interactive "
        "writes without asking, as scripts need.",
    )
    sections.output.add_argument(  # earlier spelling, kept for scripts
        "--non-interactive", dest="interactive", action="store_false", help=argparse.SUPPRESS
    )
    sections.output.add_argument(
        "--helper-metadata",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write .towel-helpers.json with call-site changes for rename-helpers (default: on). "
        "--no-helper-metadata leaves it out; rename-helpers then shows no call-site changes.",
    )
    _add_json_flag(sections.output)
    _add_shared_options(sections)

    _add_import_layout_flags(parser)
    _add_progress_flag(
        parser,
        detail="'detail' logs each pass's discovered proposals and follow-ups instead of a bar",
    )


_LONGEST_TIME_LIMIT = 100_000_000
"""The longest interval, in seconds, every supported platform's interval timer accepts.

macOS refuses a longer ``setitimer`` interval with EINVAL, and a value past
the platform's ``time_t`` overflows; either once ended a run in a traceback
(user audit CLI-09). It is about three years.
"""


def _jobs(text: str) -> int:
    try:
        count = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("jobs must be a positive integer") from error
    if count < 1:
        raise argparse.ArgumentTypeError("jobs must be a positive integer")
    return count


def _seconds(text: str) -> float:
    """A finite positive wall-clock budget the interval timer can hold."""
    try:
        seconds = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("time limit must be a positive finite number") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("time limit must be a positive finite number")
    if seconds > _LONGEST_TIME_LIMIT:
        raise argparse.ArgumentTypeError(
            f"time limit must be at most {_LONGEST_TIME_LIMIT} seconds (about three years);"
            " leave out --time-limit for no limit"
        )
    return seconds


def _add_shared_options(sections: _Sections) -> None:
    """Add the options dry and preview share, each to its section of the help.

    preview runs dry's pipeline with every one of them
    (:meth:`PreviewOptions.dry_options`), so an option added here is added
    to both dataclasses too. Within a section they follow the options the
    command added first.
    """
    sections.advanced.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore [tool.towel] defaults in the project's pyproject.toml (default: read them). "
        "Command-line options override project defaults.",
    )
    _add_report_flags(sections.output)
    _add_exclude_flag(sections.scope)
    _add_cross_module_flag(sections.scope)
    sections.limits.add_argument(
        "--jobs",
        type=_jobs,
        default=None,
        metavar="N",
        help="Use at most N pair workers (default: TOWEL_WORKERS, otherwise the smaller of "
        "8, the core count and what fits in memory; 1 runs serially).",
    )
    sections.scope.add_argument(
        "--include-ignored",
        action="store_true",
        help="Analyse files ignored by git (default: skip them; build directories stay excluded).",
    )
    _add_limit_flags(sections.limits)
    _add_tool_flags(sections.advanced)
    _add_strict_flag(sections.advanced)
    _add_parameterize_builtins_flag(sections.advanced)


def _add_json_flag(options: argparse._ActionsContainer) -> None:
    """Add ``--json``: to dry's output section, or to preview's choice of presentation."""
    options.add_argument(
        "--json",
        action="store_true",
        help="Print the result as one JSON object on stdout, for scripts (docs/COMPATIBILITY.md "
        "lists its fields); messages go to stderr. dry also needs --no-interactive.",
    )


def _add_report_flags(options: argparse._ActionsContainer) -> None:
    """Add the options that print more about a run without changing what it does."""
    options.add_argument(
        "--explain",
        action="store_true",
        help="After the run, list every place with similar code that was left alone, largest "
        "first, with its files, lines and the reason in a sentence (default: off, which lists "
        f"the {LARGEST_SHOWN} largest).",
    )
    options.add_argument(
        "--stats",
        dest="stats",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="After the run, count the pairs of similar blocks its last analysis compared and "
        "what became of each, and the helper signatures the type checker accepted and refused "
        "(default: off). The result is the same with or without it.",
    )


def _add_limit_flags(options: argparse._ActionsContainer) -> None:
    """Add the options that bound what a run considers and how long it works."""
    options.add_argument(
        "--min-lines",
        type=_Count(f"give the fewest lines a duplicate may span (default: {DEFAULT_MIN_LINES})"),
        default=DEFAULT_MIN_LINES,
        metavar="N",
        help=f"Consider only duplicated code at least N lines long (default: {DEFAULT_MIN_LINES}).",
    )
    options.add_argument(
        "--max-parameters",
        type=_Count(
            "give the most parameters a helper may take for the differences (default: "
            f"{DEFAULT_MAX_PARAMETERS})"
        ),
        default=DEFAULT_MAX_PARAMETERS,
        metavar="N",
        help="Extract only duplicates whose differences need at most N parameters (default: "
        f"{DEFAULT_MAX_PARAMETERS}); each distinct difference becomes a parameter of the helper. "
        "Variables the copies read are passed as further arguments and usually do not count "
        "(docs/GENERATED_CODE.md says when they do).",
    )
    options.add_argument(
        "--max-refactorings",
        dest="max_refactorings",
        type=_REFACTORING_COUNT,
        default=DEFAULT_MAX_ITERATIONS,
        metavar="N",
        help="Stop after applying N refactorings; 0 means no limit, so the run goes on until "
        f"nothing more can be extracted (default: {DEFAULT_MAX_ITERATIONS}).",
    )
    options.add_argument(  # earlier spelling, kept for scripts; after the default it shares
        "--max-iterations",
        dest="max_refactorings",
        type=_REFACTORING_COUNT,
        metavar="N",
        help=argparse.SUPPRESS,
    )
    options.add_argument(
        "--time-limit",
        type=_seconds,
        metavar="SECONDS",
        help="Give up if setup, analysis and checking take more than SECONDS: nothing is "
        "written and the exit status is 124 (default: no limit). Writing, once started, "
        "always finishes.",
    )
    options.add_argument(
        "--max-pairs",
        type=_Count("give the most pairs of similar blocks to compare, or 0 for no limit"),
        default=DEFAULT_MAX_CANDIDATE_PAIRS,
        metavar="N",
        help="Compare at most N pairs of similar blocks in one analysis; beyond that, the "
        "largest groups of similar blocks are skipped with a warning (default: "
        f"{DEFAULT_MAX_CANDIDATE_PAIRS:,}; 0 means no limit).",
    )


def _add_tool_flags(options: argparse._ActionsContainer) -> None:
    """Add the options that choose whether the project's type checker and formatter run."""
    options.add_argument(
        "--types",
        dest="types",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Annotate the helpers Towel writes in code that already uses type annotations, "
        "and keep only changes that add no type error, checked with the project's mypy or "
        "pyright (the 'types' extra) when installed. Default: on; --no-types writes "
        "unannotated helpers and runs no checker, which is faster.",
    )
    options.add_argument(
        "--format",
        dest="format",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Format the code Towel writes with the project's formatter (Ruff if the project "
        "configures it, else Black; the 'format' extra) and sort the imports it adds the way "
        "the project does. Default: on; --no-format inserts the code without a formatter.",
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
        help="Progress display mode: 'auto' (default) shows a transient bar on a terminal, "
        "using tqdm when installed or the built-in display otherwise; pipes show no bar. "
        "'tqdm' also warns when tqdm is not installed; 'none' disables progress; "
        f"{detail}.",
    )

    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress and notes; keep warnings, errors and results. Default: off.",
    )


def _add_preview_parser(subparsers: "argparse._SubParsersAction[_TowelParser]") -> None:
    """Add 'preview' subcommand parser."""
    parser = subparsers.add_parser(
        "preview",
        allow_abbrev=False,
        help="Show what dry would change, changing nothing",
        formatter_class=_HelpFormatter,
        description="""\
Show what towel dry would change, without changing any file: preview runs the
same complete refactoring as dry, with its type checks and formatting, on a
temporary copy, then prints a summary and a unified diff.""",
        epilog="""\
Examples, in a directory holding src/:
  # The summary and diff of everything dry would change
  towel preview src/

  # Only the summary: how many refactorings, and any that were declined
  towel preview src/ --summary

  # A faster, rougher list, without type checks or formatting
  towel preview src/ --quick""",
    )

    parser.add_argument(
        "target", help="The file or directory to examine; preview changes nothing in it."
    )
    sections = _sections(parser)
    sections.output.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 3 if the complete preview would change code, or 0 if it would "
        "not (default: off). Failures keep their own status; cannot be used with --quick.",
    )
    presentation = sections.output.add_mutually_exclusive_group()
    presentation.add_argument(
        "--summary", action="store_true", help="Print the summary only, without the diff."
    )
    presentation.add_argument(
        "--diff", action="store_true", help="Print the unified diff only, e.g. to save it."
    )
    _add_json_flag(presentation)
    sections.output.add_argument(
        "--quick",
        action="store_true",
        help="Faster and rougher: list what a single analysis finds, without the type checks, "
        "formatting and further passes of a full run, so a full run may extract more or less.",
    )
    sections.output.add_argument(
        "--inventory",
        action="store_true",
        help="Also print, as JSON, each helper the run would create, with its parameters and "
        "call sites, for choosing their names (see towel rename-helpers).",
    )
    sections.output.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=argparse.SUPPRESS,  # accepted so a dry command line previews; nothing to ask
    )
    sections.output.add_argument("--non-interactive", action="store_true", help=argparse.SUPPRESS)
    _add_shared_options(sections)
    _add_progress_flag(
        parser,
        detail="'detail' logs each complete-preview pass; --quick performs only one analysis",
    )

    _add_import_layout_flags(parser)


def _add_recover_parser(subparsers: "argparse._SubParsersAction[_TowelParser]") -> None:
    """Add 'recover' subcommand parser."""
    parser = subparsers.add_parser(
        "recover",
        allow_abbrev=False,
        help="Undo an interrupted in-place run, from the journal it left",
        formatter_class=_HelpFormatter,
        description="""\
Undo an in-place run that was interrupted while it wrote files, by a crash or
a killed process. Such a run leaves a journal, a .towel-transaction-<id>
directory holding the original files, in the closest directory that holds
every file it was changing; the next in-place run that would change those
files is refused and names the journal. recover puts every original back and
removes the journal.

If you have edited one of those files since the interruption, recover changes
nothing: it keeps your edit and the journal, and names the file to resolve
first.""",
        epilog="""\
Example, after an in-place run on src/ was interrupted:
  towel recover src/.towel-transaction-<id>""",
    )
    parser.add_argument(
        "journal",
        type=Path,
        help="The .towel-transaction-<id> directory the interrupted run left.",
    )


def _add_rename_helpers_parser(
    subparsers: "argparse._SubParsersAction[_TowelParser]",
) -> None:
    """Add 'rename-helpers' subcommand parser."""
    parser = subparsers.add_parser(
        "rename-helpers",
        allow_abbrev=False,
        help="Give the generated helpers meaningful names, updating every call",
        formatter_class=_HelpFormatter,
        description="""\
Rename the helpers Towel generated (__extracted_func_0 and so on) to
meaningful names, updating every call.

Without --rename-file, rename-helpers prints a prompt, showing each helper's
code, for you to give an AI assistant such as Claude or ChatGPT; paste the
assistant's JSON answer back and the names are applied. With --rename-file,
it applies a JSON file that maps old names to new ones, such as
  {"__extracted_func_0": "order_total"}

Give the project's root, so that Towel sees every file that uses the helpers:
the directory with pyproject.toml, setup.cfg or setup.py, or a package (a
directory with __init__.py). If it has none of these, add --complete-scope to
declare that the directory you give holds every such file. A file outside it
is never changed: a rename it would need is refused. --list needs neither.""",
        epilog="""\
Examples, in a directory holding src/:
  # List the helpers Towel generated
  towel rename-helpers src/ --list

  # Name them with an AI assistant (src/ holds every file that uses them)
  towel rename-helpers src/ --complete-scope""",
    )

    _add_progress_flag(parser, "'detail' shows diagnostic detail where available")

    parser.add_argument(
        "target",
        help="The project directory: it must hold the helpers and every file that uses them.",
    )

    parser.add_argument(
        "--complete-scope",
        action="store_true",
        help="Declare that the target directory holds every file that uses the helpers. Needed "
        "only when Towel cannot find the project's root: no pyproject.toml, setup.cfg or "
        "setup.py, and the target is not a package (default: off).",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List the helpers Towel generated (named like __extracted_func_0), renaming "
        "nothing.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON for scripts: with --list, every helper with its parameters, call "
        "sites and the keys that rename them; with --rename-file, the result (a rejected "
        "mapping exits with status 2).",
    )
    parser.add_argument(
        "--preview",
        dest="preview",
        action="store_true",
        help="Show what would be renamed without changing any file.",
    )
    parser.add_argument(  # earlier spelling, kept for scripts
        "--dry-run", dest="preview", action="store_true", help=argparse.SUPPRESS
    )

    parser.add_argument(
        "--rename-file",
        type=Path,
        metavar="FILE",
        help="Apply the renames in this JSON file, an object from old names to new, such as "
        '{"__extracted_func_0": "order_total"}. Default: none; rename-helpers prepares a '
        "prompt for an AI assistant instead.",
    )

    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        metavar="FILE",
        help="Only consider helpers defined in FILE, a path relative to the target (repeatable; "
        "default: every file).",
    )

    parser.add_argument(
        "--function",
        action="append",
        dest="functions",
        metavar="NAME",
        help="Only consider the helper named NAME (repeatable; default: every helper).",
    )

    parser.add_argument(
        "--llm",
        choices=["claude", "gpt", "copilot", "generic"],
        default=None,
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--format",
        action="store_true",
        help="Format touched files with the project's configured formatter (Ruff or Black; "
        "default: off).",
    )


CHANGE_SIDECAR_NAME = ".towel-helpers.json"


def _change_sidecar_path(target: "Path") -> "Path":
    """Where the before/after record lives for a dry output target (file or dir)."""
    target = Path(target)
    if target.is_dir():
        return target / CHANGE_SIDECAR_NAME
    return target.with_name(target.name + CHANGE_SIDECAR_NAME)


def _change_records(
    engine: "UnificationRefactorEngine", output: str
) -> Dict[str, List[ChangeRecord]]:
    """The run's call-site history, relative to its destination."""
    out = Path(output)
    base = (out if out.is_dir() else out.parent).resolve()
    helpers: Dict[str, List[ChangeRecord]] = {}
    for record in engine.change_log:
        try:
            rel = os.path.relpath(Path(record.path).resolve(), base)
        except ValueError:
            rel = record.path
        helpers.setdefault(record.helper, []).append(
            {"file": rel, "line": record.line, "before": record.before, "after": record.after}
        )
    return helpers


def _write_change_sidecar(engine: "UnificationRefactorEngine", output: str) -> None:
    """Persist each applied extraction's original block and generated call.

    Grouped by helper name so the rename-helpers inventory can show a
    before/after per call site, which helps a naming assistant finish names,
    docstrings, and types. Written next to the refactored output; delete it
    once naming is done. Each ``file`` is relative to the output, both
    resolved: the engine records resolved paths, and an output reached
    through a link (macOS's ``/tmp``) once gave every record a path climbing
    out of the link's spelling into the resolved one, which the inventory,
    matching ``file`` against paths relative to its target, never found.
    """

    helpers = _change_records(engine, output)
    if not helpers:
        return
    out = Path(output)
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


def _verification_cost(
    project_path: "Path", types: bool, excluded: "Sequence[str]" = ()
) -> List[str]:
    """What a typed run over this project involves, in facts rather than a forecast.

    How long a run takes is governed by how many proposals the project rejects,
    and that is not known until it has rejected them: on Sphinx the rejections
    outnumbered the refactorings about twenty-five to one late in the run. A
    figure derived from anything available here would be confident and wrong,
    so this states what is known and leaves the judgement to the reader.

    It names the checkers the run will start, chosen as the run chooses them
    but without starting one, since the user has not yet agreed to the run.
    When none will run (none is installed, or a configured one is missing and
    the run will refuse or proceed unverified) there is no verification to cost.
    The files counted are those the run analyzes, less ``excluded``.
    """
    from towel.type_inference import CheckerNotInstalled, type_checkers_for_project

    if not types or not project_path.is_dir():
        return []
    try:
        files = python_sources(project_path, excluded=excluded)
    except OSError:
        # Only for a message; a real I/O problem resurfaces in the analysis.
        return []
    if len(files) < _COST_NOTE_FILES:
        return []
    try:
        checkers = type_checkers_for_project(project_path)
    except CheckerNotInstalled:
        return []  # Selection reports refusal or unverified fallback before any prompt.
    if not checkers:
        return []
    packages = _import_breadth(files, project_path)
    return [
        f"Type checking is on ({' and '.join(checkers)}), and this is where the time goes. Every",
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
    """Select the project's checker, raising when a configured checker is missing."""
    from towel.type_inference import type_oracle_for_project

    return type_oracle_for_project(project_path).tool


def _target_has_annotations(project_path: Path, excluded: Sequence[str]) -> bool:
    """Whether any selected source has annotations an optional checker could verify."""
    for path in python_sources(project_path, excluded=excluded):
        try:
            tree = ast.parse(read_source(path), type_comments=True)
        except (OSError, UnicodeError, SyntaxError, ValueError):
            continue  # This census only chooses a note; analysis handles unreadable source.
        if any(
            isinstance(node, ast.AnnAssign)
            or isinstance(node, ast.arg)
            and (node.annotation is not None or node.type_comment is not None)
            or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (node.returns is not None or node.type_comment is not None)
            for node in ast.walk(tree)
        ):
            return True
    return False


def _generated_code_formatter(
    project_path: "Path",
) -> Callable[[str], Optional[Callable[[str], str]]]:
    """Select each modified file's formatter, reporting unavailable project tools."""
    from towel.formatting import SnippetFormatter, formatter_factory_for_project
    from towel.project_tools import ToolChoice

    reported: set[str] = set()

    def report(choice: ToolChoice[SnippetFormatter]) -> None:
        if choice.note in reported:
            return
        reported.add(choice.note)
        if choice.tool is None:
            write_note(
                f"Note: {choice.note}, so generated code is inserted unformatted. "
                'Install the format extra (pip install "code-towel[format]") to format it.',
            )
        elif "not installed" in choice.note:
            write_note(
                f"Note: {choice.note}; formatting generated code with {choice.note.split(';')[0]}.",
            )

    return formatter_factory_for_project(project_path, report)


def _file_finisher(
    project_path: "Path", *, sort_imports: bool
) -> Optional[Callable[[str, str], str]]:
    """The project's import sorting and Ruff lint guard, with missing-tool notes."""
    from towel.formatting import file_finisher_for_project

    choice = file_finisher_for_project(project_path, sort_imports=sort_imports)
    if "not installed" in choice.note:
        write_note(f"Note: {choice.note}.")
    return choice.tool


def _banner(title: str) -> None:
    """Print a step heading the way every command does."""
    print(title)
    print("=" * 70)
    print()


def _existing_target(path: str) -> Tuple[bool, bool]:
    """``(is_file, is_dir)`` for a path the command may work on; exits with a message otherwise."""

    if not os.path.exists(path):
        raise ValueError(f"'{path}' does not exist")
    is_file, is_dir = os.path.isfile(path), os.path.isdir(path)
    if not is_file and not is_dir:
        raise ValueError(f"'{path}' is neither a file nor a directory")
    return is_file, is_dir


def _resolve_cli_path(path: str) -> Path:
    """Resolve a user path, reporting older pathlib's symlink-loop failure normally."""
    try:
        return Path(path).resolve()
    except RuntimeError as error:
        # Python 3.11/3.12 report symlink loops as RuntimeError. Keep this
        # conversion local to resolution so unrelated programming errors surface.
        raise ValueError(f"Cannot resolve path {path!r}: {error}") from error


@dataclass(frozen=True)
class DryOptions:
    """One dry pipeline, publishing to a requested or private preview destination."""

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
    stats: bool = False
    json: bool = False
    summary: bool = False
    diff: bool = False
    inventory: bool = False
    strict: bool = DEFAULT_STRICT
    explain: bool = False
    quiet: bool = False
    jobs: Optional[int] = None
    include_ignored: bool = False
    config_path: Optional[str] = None
    helper_metadata: bool = True

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
            strict=bool(args.strict),
            format=bool(args.format),
            exclude=tuple(args.exclude or ()),
            cross_module=bool(args.cross_module),
            stats=bool(args.stats),
            json=bool(args.json),
            summary=bool(getattr(args, "summary", False)),
            diff=bool(getattr(args, "diff", False)),
            inventory=bool(getattr(args, "inventory", False)),
            explain=bool(getattr(args, "explain", False)),
            quiet=bool(getattr(args, "quiet", False)),
            jobs=getattr(args, "jobs", None),
            include_ignored=bool(getattr(args, "include_ignored", False)),
            config_path=getattr(args, "config_path", None),
            helper_metadata=bool(getattr(args, "helper_metadata", True)),
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
    max_refactorings: int
    types: bool
    format: bool
    quick: bool
    check: bool = False
    exclude: Tuple[str, ...] = ()
    cross_module: bool = False
    stats: bool = False
    json: bool = False
    summary: bool = False
    diff: bool = False
    inventory: bool = False
    strict: bool = DEFAULT_STRICT
    explain: bool = False
    quiet: bool = False
    jobs: Optional[int] = None
    include_ignored: bool = False
    config_path: Optional[str] = None

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "PreviewOptions":
        return cls(
            target=str(args.target),
            min_lines=int(args.min_lines),
            max_parameters=int(args.max_parameters),
            parameterize_builtins=bool(args.parameterize_builtins),
            max_pairs=int(args.max_pairs),
            progress=normalize_progress(args.progress),
            max_refactorings=int(args.max_refactorings),
            types=bool(args.types),
            strict=bool(args.strict),
            format=bool(args.format),
            quick=bool(args.quick),
            check=bool(getattr(args, "check", False)),
            exclude=tuple(args.exclude or ()),
            cross_module=bool(args.cross_module),
            stats=bool(args.stats),
            json=bool(args.json),
            summary=bool(getattr(args, "summary", False)),
            diff=bool(getattr(args, "diff", False)),
            inventory=bool(getattr(args, "inventory", False)),
            explain=bool(getattr(args, "explain", False)),
            quiet=bool(getattr(args, "quiet", False)),
            jobs=getattr(args, "jobs", None),
            include_ignored=bool(getattr(args, "include_ignored", False)),
            config_path=getattr(args, "config_path", None),
        )

    def dry_options(self, output: str) -> DryOptions:
        """Run with the same options, without asking before writing private output."""
        return DryOptions(
            input=self.target,
            output=output,
            interactive=False,
            max_refactorings=self.max_refactorings,
            min_lines=self.min_lines,
            max_parameters=self.max_parameters,
            parameterize_builtins=self.parameterize_builtins,
            max_pairs=self.max_pairs,
            progress=self.progress,
            types=self.types,
            strict=self.strict,
            format=self.format,
            exclude=self.exclude,
            cross_module=self.cross_module,
            stats=self.stats,
            json=self.json,
            summary=self.summary,
            diff=self.diff,
            inventory=self.inventory,
            explain=self.explain,
            quiet=self.quiet,
            jobs=self.jobs,
            include_ignored=self.include_ignored,
            config_path=self.config_path,
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
    llm: Optional[str]
    complete_scope: bool
    format: bool = False

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "RenameOptions":
        raw_llm = getattr(args, "llm", None)
        return cls(
            target=Path(args.target),
            files=list(args.files) if args.files else None,
            functions=list(args.functions) if args.functions else None,
            rename_file=Path(args.rename_file) if args.rename_file else None,
            list=bool(args.list),
            json=bool(args.json),
            preview=bool(args.preview),
            llm=str(raw_llm) if raw_llm is not None else None,
            complete_scope=bool(args.complete_scope),
            format=bool(getattr(args, "format", False)),
        )


def _run_dry(args: argparse.Namespace) -> None:
    """Run the dry command."""
    options = DryOptions.from_namespace(args)
    _warn_about_retired_flags(args)
    if options.json and options.interactive:
        raise ValueError("dry --json requires --no-interactive")
    if options.json:
        with redirect_stdout(sys.stderr):
            result = _execute_dry(options)
        print(json.dumps(result, indent=2))
    else:
        _execute_dry(options)


def _execute_dry(options: DryOptions, *, preview: bool = False) -> "CommandResult":
    from towel.source_files import source_policy

    with source_policy(strict=options.strict, include_ignored=options.include_ignored):
        return _execute_dry_with_policy(options, preview=preview)


def _execute_dry_with_policy(options: DryOptions, *, preview: bool = False) -> "CommandResult":
    """Run the shared pipeline; only its presentation and destination differ for preview."""
    # Import here to avoid loading heavy modules if not needed
    from towel.unification.refactor_engine import UnificationRefactorEngine

    input_path = options.input
    output_path = options.output

    is_file, is_dir = _existing_target(input_path)

    if is_file and not input_path.endswith(".py"):
        # The file was named explicitly, so it is analysed; the census says
        # whether it was read as Python.
        print(
            f"Warning: '{input_path}' is not a Python file (.py); analysing it as one.",
            file=sys.stderr,
        )
    if is_file and not preview and output_path.endswith(("/", os.sep)):
        raise ValueError(
            f"The input is one file, so the output must name a file, not the directory"
            f" '{output_path}'; for example: towel dry {shlex.quote(input_path)}"
            f" {shlex.quote(output_path + Path(input_path).name)}"
        )

    # Resolve aliases before checking containment or creating any output.
    source = _resolve_cli_path(input_path)
    destination = _resolve_cli_path(output_path)
    if source != destination:
        if source in destination.parents or destination in source.parents:
            raise ValueError("Input and output must not contain one another")
        if destination.exists() or Path(output_path).is_symlink():
            raise ValueError(
                "Output already exists; choose a new path"
                " (to refactor in place, give the same path twice)"
            )
    if source != destination and not preview:
        from towel.filesystem import refuse_unusable_output

        refuse_unusable_output(source, destination)
    refuse_unparsed_program(source, options.exclude)
    if options.cross_module and is_dir:
        _judge_import_problems(source, options.exclude)

    if not preview:
        print("=" * 70)
        _banner("APPLYING REFACTORINGS (FIXED-POINT ITERATION)")
        print("This will apply refactorings one at a time until no more are found.")
        print("Helpers go before the first definition of their module,")
        print("or inside the class or function the duplicates share.")
        if options.interactive:
            print("Nothing is written until you confirm, once the run knows what it would change.")
        print()

    if options.interactive:
        for line in _verification_cost(Path(input_path), options.types, options.exclude):
            print(line)

    journal = _pending_journal(source, options.exclude)
    if journal is not None and source == destination:
        raise ValueError(
            "Refusing to refactor in place: "
            + pending_journal_remedy(journal, "this run would change")
        )
    if journal is not None:
        # Out of place the run changes nothing the journal names, so it is
        # not blocked, as preview is not; but it refactors those files as an
        # interrupted change left them, which the user should know first.
        LOG.warning("%s", pending_journal_remedy(journal, "this output is refactored from"))

    from towel.type_baseline import UNTYPED_REMEDY
    from towel.type_inference import CheckerNotInstalled, type_checkers_for_project

    annotate_helpers = options.types
    ask = _ask_before_writing(options) if options.interactive and not preview else None
    try:
        if options.types:
            type_checkers_for_project(source)
    except CheckerNotInstalled as error:
        if options.strict:
            raise
        annotate_helpers = False
        print(
            f"Warning: {error.explanation}. Continuing without type verification; generated "
            "helpers stay bare. Use --strict to refuse instead.",
            file=sys.stderr,
        )
    oracle: Optional[TypeOracle] = None
    try:
        oracle = _type_oracle(source) if annotate_helpers else None
        if oracle is None and annotate_helpers and _target_has_annotations(source, options.exclude):
            # Through the progress lane's display, which --quiet silences.
            write_note(
                "Note: neither mypy nor pyright is installed, so helper annotations are copied "
                "from the call sites but not inferred or verified. Install the types extra "
                '(pip install "code-towel[types]").'
            )
        types_status: TypesStatus = (
            "off" if not options.types else "verified" if oracle is not None else "unverified"
        )
        engine = UnificationRefactorEngine(
            max_parameters=options.max_parameters,
            min_lines=options.min_lines,
            parameterize_builtins=options.parameterize_builtins,
            strict=options.strict,
            max_candidate_pairs=options.max_pairs,
            settings=_execution_settings(options.jobs),
            include_ignored=options.include_ignored,
            parameterize_constants=True,
            excluded_directories=options.exclude,
            cross_module_helpers=options.cross_module,
            snippet_formatter_for_file=(
                _generated_code_formatter(Path(input_path)) if options.format else None
            ),
            file_finisher=_file_finisher(Path(input_path), sort_imports=options.format),
            annotate_helpers=annotate_helpers,
            type_oracle=oracle,
        )

        if not preview:
            print()

        findings: Tuple[RelaxedFinding, ...] = ()
        if not options.strict:
            paths = (
                [source]
                if is_file
                else [Path(path) for path in engine._find_python_files(str(source))]
            )
            findings = findings_for_paths(paths, root=source.parent if is_file else source)
            if findings:
                write_note(relaxed_notice(findings))

        content: Optional[_PreviewContent] = None

        def capture(stage: StagedProject) -> None:
            nonlocal content
            content = _preview_content(engine, options, stage)

        results: Dict[str, Tuple[int, List[str]]]
        if is_file:
            if not preview:
                print(f"Refactoring file: {output_path}")
            _, applied, descriptions = engine.refactor_to_fixed_point(
                input_path,
                max_iterations=options.max_refactorings,
                progress=options.progress,
                output_path=output_path,
                before_publication=ask,
                before_preview=capture if preview else None,
            )
            termination = (
                "time_limit"
                if engine.preview_incomplete
                else "iteration_cap" if 0 < options.max_refactorings <= applied else "fixed_point"
            )
            results = {output_path: (applied, descriptions)} if applied else {}
        else:
            if not preview:
                print(f"Refactoring directory: {output_path}")
            results, termination_reason = engine.refactor_directory_to_fixed_point(
                input_path,
                output_path,
                max_iterations=options.max_refactorings,
                progress=options.progress,
                before_publication=ask,
                before_preview=capture if preview else None,
            )
            applied = sum(count for count, _ in results.values())
            termination = termination_reason

        report = engine.run_report
        relative = _relative_to_run(options)
        if preview:
            _print_complete_preview(engine, options, applied, results, termination, content=content)
        else:
            _print_dry_summary(report, options, applied, results, termination, relative)
        if engine.checker_failures:
            # Applied refactorings were verified; these were not judged at all.
            print(
                f"\n{engine.checker_failures} proposal(s) were dropped because the type checker"
                " could not run for them (see the warnings above); they were not judged."
                f" {UNTYPED_REMEDY}"
            )
        if not preview and not (options.json or options.diff):
            for line in _left_alone_report(report, options, applied, relative):
                print(line)
        if options.stats:
            _print_statistics(report)
        if not preview and options.helper_metadata:
            # Publication has succeeded. This optional naming aid cannot
            # retroactively make that source transaction a failed command.
            try:
                _write_change_sidecar(engine, output_path)
            except (OSError, ValueError) as error:
                LOG.warning(
                    "Source publication succeeded, but the optional naming sidecar"
                    " could not be written: %s. Helper inventory and renaming remain"
                    " available without call-site before/after metadata.",
                    error,
                )
        return _command_result(
            engine,
            options,
            applied,
            termination,
            preview=preview,
            types_status=types_status,
            content=content,
            findings=findings,
        )
    finally:
        if oracle is not None:
            oracle.close()


class RunCancelled(TowelError):
    """The user declined to write what a finished ``dry`` run would change."""


def _shown_path(path: str, is_dir: bool) -> str:
    """``path`` as the user gave it, ending in a separator when it names a directory."""
    return path if not is_dir or path.endswith(("/", os.sep)) else path + "/"


def _ask_before_writing(options: DryOptions) -> "BeforePublication":
    """The question ``dry`` asks once it knows what it would write, before writing it.

    It is not asked when there is nothing to apply. Answering anything but
    ``y`` or ``yes`` (any case), or no answer at all, cancels the run:
    nothing is written.
    """
    from towel.unification.fixed_point import Publication

    relative = _relative_to_run(options)

    def ask(publication: Publication) -> None:
        if not publication.applied:
            return
        sys.stdout.flush()
        files = [relative(path) for path in publication.files]
        listed = f": {', '.join(files)}" if len(files) <= LARGEST_SHOWN else ""
        if publication.in_place:
            where = (
                f"will change {_shown_path(options.input, Path(options.input).is_dir())} in place"
            )
        else:
            where = f"will write {_shown_path(options.output, Path(options.input).is_dir())}"
        print(
            f"\n{publication.applied} refactoring(s) in {len(files)} file(s){listed}; {where}.",
            file=sys.stderr,
        )
        if publication.in_place:
            dirty = _uncommitted(publication.files)
            if dirty:
                print(
                    "Warning: these files have changes not committed to git, which this run"
                    f" would change further: {', '.join(relative(path) for path in dirty)}",
                    file=sys.stderr,
                )
        if not sys.stdin.isatty():
            print(
                "(Input is not a terminal; to write without this question, pass"
                " --no-interactive.)",
                file=sys.stderr,
            )
        if not _confirm("Write these changes? [y/N] "):
            raise RunCancelled("Cancelled; nothing was written.")

    return ask


def _uncommitted(files: Sequence[str]) -> List[str]:
    """Which of ``files`` git reports changed and not committed; none outside a work tree.

    Only a warning rests on this, so a missing git, a directory that is not
    a work tree, or a git that fails says nothing rather than stopping the run.
    """
    git = shutil.which("git")
    if git is None or not files:
        return []
    try:
        top = subprocess.run(  # nosec B603: git from PATH, fixed arguments
            [git, "rev-parse", "--show-toplevel"],
            cwd=Path(files[0]).parent,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if top.returncode != 0:
            return []
        root = Path(top.stdout.strip())
        # Porcelain paths are relative to the work tree's root.
        status = subprocess.run(  # nosec B603: git from PATH, fixed arguments
            [git, "status", "--porcelain", "-z", "--", *files],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if status.returncode != 0:
        return []
    changed = {
        (root / entry[3:].decode("utf-8", "surrogateescape")).resolve()
        for entry in status.stdout.split(b"\0")
        if len(entry) > 3
    }
    return [path for path in files if Path(path).resolve() in changed]


def _relative_to_run(options: DryOptions) -> Callable[[str], str]:
    """How a run's report names a file: relative to the target, or to a file target's directory.

    The engine names files by the output it writes (the target itself in
    place, a private copy for preview); either way the part below the target
    is the user's own path. A file outside both is named as it was given.
    """
    target_is_dir = Path(options.input).is_dir()
    bases = []
    for given in (options.output, options.input):
        resolved = _resolve_cli_path(given)
        bases.append(resolved if target_is_dir else resolved.parent)

    def relative(path: str) -> str:
        for candidate in (Path(os.path.abspath(path)), _resolve_cli_path(path)):
            for base in bases:
                if candidate.is_relative_to(base) and candidate != base:
                    return str(candidate.relative_to(base))
        return path

    return relative


def _print_dry_summary(
    report: "RunReport",
    options: DryOptions,
    applied: int,
    results: Mapping[str, Tuple[int, List[str]]],
    termination: str,
    relative: Callable[[str], str],
) -> None:
    """What a finished ``dry`` read and did, in the words every command uses."""
    print()
    print(census_line(report))
    outcome = outcome_line(report, applied, options.input)
    if outcome is not None:
        print(outcome)
    else:
        print(f"Applied {applied} refactoring(s) across {len(results)} file(s).")
        _print_changed_files(results, relative)
    stopped = stopped_at_limit(termination, options.max_refactorings)
    if stopped:
        print(stopped)


def _print_changed_files(
    results: Mapping[str, Tuple[int, List[str]]], relative: Callable[[str], str]
) -> None:
    """Each changed file, with the first few of its refactorings."""
    for file_path, (count, descriptions) in sorted(results.items()):
        print(f"\n  {relative(file_path)}: {count} refactoring(s)")
        for desc in descriptions[:3]:
            print(f"    - {desc}")
        if len(descriptions) > 3:
            print(f"    ... and {len(descriptions) - 3} more")


def _left_alone_report(
    report: "RunReport", options: DryOptions, applied: int, relative: Callable[[str], str]
) -> List[str]:
    """Why code was left alone, then where: the largest places, or with --explain all of them."""
    pairs = shows_pairs(applied, options.explain)
    problem = report.first_unverifiable_reason
    if problem:
        # The typed lane's detail: the first unresolved name, in the user's own path.
        target = Path(options.input).resolve()
        root = target if target.is_dir() else target.parent
        problem = problem.removeprefix(str(root) + os.sep)
    return reason_lines(report, pairs=pairs, first_unverifiable=problem) + left_alone_lines(
        report, relative, every=options.explain, pairs=pairs
    )


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

LACKING_MODULE_REMEDY = (
    "For a directory lacking a module the program imports under its name: if the program means"
    " a library of that name, leave the directory out with --exclude <directory name>, or rename"
    " it; if it means the directory, fix that import, or leave out the directory holding it with"
    " --exclude <directory name>."
)
"""What a user can do about a name whose one location lacks a module the program imports of it."""

LINKED_FILE_REMEDY = (
    "For a file a symbolic or hard link gives a second name: replace the link with a copy of what"
    " it names, or make the program import that file by one name only."
)
"""What a user can do about a file reachable under two names through a link."""

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
    imports give its host (docs/design/DECISIONS.md, "Import names come from the
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
    and a directory named like a distribution the project requires, or like
    a library holding a module it lacks, is resolved only by telling the two
    apart.
    """
    from towel.import_model import Doubt

    remedies = {
        Doubt.TREE: STRAY_COPY_REMEDY,
        Doubt.INSTALLED: INSTALLED_COPY_REMEDY,
        Doubt.REQUIRED: REQUIRED_DISTRIBUTION_REMEDY,
        Doubt.LACKING: LACKING_MODULE_REMEDY,
        Doubt.LINK: LINKED_FILE_REMEDY,
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


def _print_statistics(report: "RunReport") -> None:
    """Account for every candidate pair of the last whole analysis, and the run's signatures.

    The pair line adds up: each pair is declined, a duplicate, dropped for an
    overlap, or emitted, so a reader sees where the duplicates went. A run
    whose checker judged no signature leaves the signature line out.
    """
    declined = sum(report.declined_pairs.values())
    print("\nStatistics (the last whole analysis; signatures over the whole run):")
    print(
        f"  {report.candidate_pairs} candidate pair(s): {declined} declined, "
        f"{report.duplicate_pairs} duplicate, {report.overlapping_proposals} overlapping, "
        f"{report.emitted_proposals} emitted"
    )
    signatures = report.signatures_verified + report.signatures_refused
    if signatures:
        print(
            f"  {signatures} signature attempt(s): {report.signatures_verified} verified, "
            f"{report.signatures_refused} refused by the type checker"
        )
    not_applied = sum(report.declined_proposals.values())
    print(f"  {not_applied} proposal(s) not applied")


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
    """Run dry privately, or explicitly request the partial structural listing."""
    options = PreviewOptions.from_namespace(args)
    _warn_about_retired_flags(args)
    if options.quick:
        if (
            options.json
            or options.summary
            or options.diff
            or options.inventory
            or options.stats
            or options.check
        ):
            raise ValueError(
                "--quick cannot be combined with --json, --summary, --diff, --inventory, --stats or --check"
            )
        _run_quick_preview(options)
        return
    _existing_target(options.target)
    with tempfile.TemporaryDirectory(prefix="towel-preview-") as temporary:
        output = Path(temporary) / Path(options.target).resolve().name
        dry_options = options.dry_options(str(output))
        with _preview_paths(dry_options):
            if options.json or options.diff:
                with redirect_stdout(sys.stderr):
                    result = _execute_dry(dry_options, preview=True)
                if options.json:
                    print(json.dumps(result, indent=2))
                else:
                    print(result.get("partial_diff", result["diff"]), end="")
            else:
                result = _execute_dry(dry_options, preview=True)
    if result["status"] == "incomplete":
        print(
            "Time limit exceeded; partial preview is not finally confirmed. Input files were not changed.",
            file=sys.stderr,
        )
        sys.exit(124)
    if options.check and result["status"] == "complete" and result["applied"] > 0:
        sys.exit(3)


def _preview_text(text: str, options: DryOptions) -> str:
    """Name the input wherever the private output would appear in a report."""
    for output in (str(Path(options.output).resolve()), options.output):
        text = text.replace(output, str(Path(options.input).resolve()))
    return text


@contextmanager
def _preview_paths(options: DryOptions) -> Iterator[None]:
    """Translate the engine's public output paths once more for a disposable preview."""
    from towel.unification.fixed_point import _StagePathsInLogs, _name_public_paths

    # Engine staging translates its paths on the logger first. Handler filters
    # run afterward, so diagnostics name input files rather than the private
    # preview destination that is about to disappear.
    def rewrite(text: str) -> str:
        return _preview_text(text, options)

    path_filter = _StagePathsInLogs(rewrite)
    handlers = tuple(dict.fromkeys([*LOG.handlers, *logging.getLogger().handlers]))
    for handler in handlers:
        handler.addFilter(path_filter)
    try:
        yield
    except (OSError, ValueError, TowelError) as error:
        _name_public_paths(error, rewrite)
        raise
    finally:
        for handler in handlers:
            handler.removeFilter(path_filter)


@dataclass(frozen=True)
class _PreviewContent:
    diff: str
    inventory: HelperInventory | None


def _preview_content(
    engine: UnificationRefactorEngine, options: DryOptions, stage: StagedProject
) -> _PreviewContent:
    """Read the filtered private stage before it is removed, without publishing a copy."""
    staged_options = replace(options, output=str(stage.target))
    return _PreviewContent(
        source_diff(Path(options.input), stage.target, options.exclude),
        _preview_inventory(engine, staged_options) if options.inventory or options.json else None,
    )


def _print_complete_preview(
    engine: "UnificationRefactorEngine",
    options: DryOptions,
    applied: int,
    results: Mapping[str, Tuple[int, List[str]]],
    termination: str,
    *,
    content: Optional[_PreviewContent] = None,
) -> None:
    """Report only the completed pipeline's accepted changes, in their execution order.

    The census and the outcome follow the count; what would change, file by
    file, follows them, as ``dry`` lists it; why code was left alone comes
    last, before the closing line.
    """
    if options.json or options.diff:
        return
    report = engine.run_report
    relative = _relative_to_run(options)
    headline = [census_line(report)]
    if engine.preview_incomplete:
        headline.append(
            "Partial preview: these changes are not finally confirmed (time limit exceeded)."
            if applied
            else "Preview incomplete: no changes were applied before the time limit."
        )
    outcome = None if engine.preview_incomplete else outcome_line(report, applied, options.input)
    if outcome is not None:
        headline.append(outcome)
    stopped = stopped_at_limit(termination, options.max_refactorings)
    if stopped:
        headline.append(stopped)
    if results:
        headline.append(f"Would change {len(results)} file(s):")
        for file_path, (count, descriptions) in sorted(results.items()):
            headline.append(f"  {relative(file_path)}: {count} refactoring(s)")
            headline.extend(f"    - {description}" for description in descriptions)
    print(
        render_preview(
            applied,
            headline,
            ("" if options.summary else content.diff if content is not None else ""),
            content.inventory if content is not None and options.inventory else None,
            _left_alone_report(report, options, applied, relative),
        ),
        end="",
    )


def _preview_inventory(engine: "UnificationRefactorEngine", options: DryOptions) -> HelperInventory:
    source, output = Path(options.input).resolve(), Path(options.output).resolve()
    output_base = output if output.is_dir() else output.parent
    helpers = _find_extracted_helpers(output, None, None)
    inventory = helper_inventory(
        output_base, helpers, changes_by_helper=_change_records(engine, options.output)
    )
    inventory["target"] = str(source if source.is_dir() else source.parent)
    return inventory


def _command_result(
    engine: "UnificationRefactorEngine",
    options: DryOptions,
    applied: int,
    termination: str,
    *,
    preview: bool,
    types_status: TypesStatus,
    content: Optional[_PreviewContent] = None,
    findings: Sequence[RelaxedFinding] = (),
) -> CommandResult:
    inventory = None
    if options.json:
        try:
            inventory = (
                content.inventory if content is not None else _preview_inventory(engine, options)
            )
            if not preview and inventory is not None:
                output = Path(options.output).resolve()
                inventory["target"] = str(output if output.is_dir() else output.parent)
        except (OSError, ValueError) as error:
            # A naming aid is optional; a failed read after dry has committed
            # must not turn published output into a reported failed operation.
            LOG.warning("Helper inventory unavailable: %s", error)
    result: CommandResult = {
        "schema_version": 1,
        "config_path": options.config_path,
        "command": "preview" if preview else "dry",
        "mode": mode_name(options.strict),
        "status": "incomplete" if engine.preview_incomplete else "complete",
        "input": str(Path(options.input).resolve()),
        "output": None if preview else str(Path(options.output).resolve()),
        "published": not preview,
        "applied": applied,
        "termination": termination,
        "diff": (
            content.diff
            if content is not None
            and not engine.preview_incomplete
            and (options.json or options.diff)
            else ""
        ),
        "helpers": inventory,
        "error": None,
        "outcome": None if engine.preview_incomplete else run_outcome(engine.run_report, applied),
        "analysed": analysed_record(engine.run_report, _relative_to_run(options)),
        "left_alone": left_alone_count(engine.run_report, applied),
        "relaxed_findings": [finding.to_json() for finding in findings],
    }
    if engine.preview_incomplete:
        result["partial_diff"] = content.diff if content is not None else ""
        result["finally_confirmed"] = False
        result["error"] = "Time limit exceeded; partial preview is not finally confirmed"
    if types_status != "verified" or engine.type_check_ran:
        result["types"] = types_status
    return result


def _write_failure_result(
    args: argparse.Namespace,
    status: Literal["incomplete", "error"],
    termination: str,
    error: str,
) -> None:
    if args.command not in {"dry", "preview"} or not getattr(args, "json", False):
        return
    result: CommandResult = {
        "schema_version": 1,
        "config_path": getattr(args, "config_path", None),
        "command": args.command,
        "mode": mode_name(bool(getattr(args, "strict", DEFAULT_STRICT))),
        "status": status,
        # Failure reporting must not repeat the filesystem operation that
        # failed: lexical absolute paths work even for inaccessible/looped paths.
        "input": os.path.abspath(args.input if args.command == "dry" else args.target),
        "output": os.path.abspath(args.output) if args.command == "dry" else None,
        "published": False if args.command == "preview" or termination == "time_limit" else None,
        "applied": 0,
        "termination": termination,
        "diff": "",
        "helpers": None,
        "error": error,
        "outcome": None,
        "analysed": None,
        "left_alone": 0,
        "relaxed_findings": [],
    }
    try:
        print(json.dumps(result, indent=2))
    except BrokenPipeError:
        # Preserve the operation's failure status when its reader has gone.
        _close_stdout_quietly()


def _quick_report(engine: "UnificationRefactorEngine") -> "RunReport":
    """What one analysis read and declined, as a fixed-point run's report gives it."""
    from towel.unification.fixed_point import RunReport

    census = engine.last_census
    return RunReport(
        declined_pairs=engine.declined_pairs,
        candidate_pairs=engine.last_pair_tally.candidate_pairs,
        files_analysed=census.files,
        functions_analysed=census.functions,
        skipped_files=census.skipped,
        left_alone=engine.last_left_alone,
    )


def _run_quick_preview(options: PreviewOptions) -> None:
    from towel.source_files import source_policy

    with source_policy(strict=options.strict, include_ignored=options.include_ignored):
        _run_quick_preview_with_policy(options)


def _run_quick_preview_with_policy(options: PreviewOptions) -> None:
    """List one analysis without promising that the proposals will pass dry's checks."""
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

    _banner("QUICK PREVIEW (PARTIAL STRUCTURAL ANALYSIS)")
    print("Skips type checks, formatting and follow-up extractions.")
    print("This partial list is not an upper bound on eventual extractions.")
    print("Candidates may fail dry's validation; --max-refactorings does not limit this list.")

    engine = UnificationRefactorEngine(
        max_parameters=options.max_parameters,
        min_lines=options.min_lines,
        parameterize_builtins=options.parameterize_builtins,
        strict=options.strict,
        max_candidate_pairs=options.max_pairs,
        settings=_execution_settings(options.jobs),
        include_ignored=options.include_ignored,
        parameterize_constants=True,
        excluded_directories=options.exclude,
        cross_module_helpers=options.cross_module,
    )

    if not options.strict:
        source = Path(target).resolve()
        paths = [source] if is_file else [Path(path) for path in engine._find_python_files(target)]
        findings = findings_for_paths(paths, root=source.parent if is_file else source)
        if findings:
            write_note(relaxed_notice(findings))

    # Analyze
    if is_file:
        if not target.endswith(".py"):
            print(
                f"Warning: '{target}' is not a Python file (.py); analysing it as one.",
                file=sys.stderr,
            )

        print(f"Analyzing file: {target}")
        all_proposals = engine.analyze_files([target], progress=options.progress)
    else:
        print(f"Analyzing directory: {target}")
        all_proposals = engine.analyze_directory(target, recursive=True, progress=options.progress)

    report = _quick_report(engine)
    relative = _relative_to_run(options.dry_options(target))
    print()
    print(census_line(report))
    outcome = outcome_line(report, 0, target) if not all_proposals else None
    if outcome is not None:
        print(outcome)
        for line in reason_lines(report) + left_alone_lines(
            report, relative, every=options.explain
        ):
            print(line)
        return
    print(f"Found {len(all_proposals)} refactoring opportunities")

    proposals = filter_overlapping_proposals(all_proposals)

    if len(proposals) < len(all_proposals):
        print(f"Filtered to {len(proposals)} non-overlapping proposals")
        print(f"(Removed {len(all_proposals) - len(proposals)} overlapping proposals)")

    print("\n" + "=" * 70)
    print("REFACTORING OPPORTUNITIES (PARTIAL)")
    print("=" * 70)

    source_cache: Dict[str, List[str]] = {}
    for i, prop in enumerate(proposals[:10], 1):
        _print_proposal(i, prop, target, is_dir, source_cache)

    if len(proposals) > 10:
        print(f"\n... and {len(proposals) - 10} more proposals")

    print("\n" + "=" * 70)
    print("\nFor a complete preview with the same options, omit --quick.")
    print()


def _run_rename_helpers(args: argparse.Namespace) -> None:
    """Run the rename-helpers command."""
    options = RenameOptions.from_namespace(args)
    if options.llm is not None:
        LOG.warning(
            "--llm is deprecated since Towel 2.0 and will be removed in a future release; "
            "the prompt is the same for every assistant."
        )
    target = options.target

    if not target.exists():
        raise ValueError(f"'{target}' does not exist")

    if not target.is_dir():
        raise ValueError(f"'{target}' must be a directory")
    journal = _pending_journal(target)
    if journal is not None:
        LOG.warning("%s", pending_journal_remedy(journal, "a rename here would change"))

    helpers = _find_extracted_helpers(
        target,
        options.files,
        options.functions,
        include_named=bool(options.rename_file and not options.list),
    )

    if not helpers and not options.rename_file and not (options.list and options.json):
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

    from towel.renaming import inferred_rename_root

    if not options.complete_scope and inferred_rename_root(target) is None:
        reason = (
            f"No pyproject.toml, setup.cfg, setup.py or package __init__.py was found above "
            f"{target}, so Towel cannot tell whether code outside it calls these helpers; "
            "if nothing outside does, add --complete-scope."
        )
        if options.json:
            print(json.dumps({"applied": False, "dry_run": options.preview, "error": reason}))
        else:
            print(f"Error: {reason}", file=sys.stderr)
        sys.exit(2)

    if options.rename_file:
        _apply_rename_file(
            target,
            helpers,
            options.rename_file,
            options.preview,
            options.json,
            restrict_selection=bool(options.files or options.functions),
            format_files=options.format,
        )
        return

    # Interactive LLM mode
    _run_interactive_llm_mode(
        target,
        helpers,
        options.llm or "generic",
        options.preview,
        restrict_selection=bool(options.files or options.functions),
        format_files=options.format,
    )


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
            full_source = ast.get_source_segment(source, node) or "\n".join(
                lines[start : node.end_lineno or start + 5]
            )
            helpers.append((py_file, node.name, node.lineno, full_source))

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
    """Ask a yes/no question on stderr: ``y`` or ``yes``, in any case, is yes.

    Stdout carries the run's report, which a reader may be piping
    elsewhere, so the question goes where messages go. Unavailable stdin
    declines; an interrupt propagates.
    """
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        answer = sys.stdin.readline()
    except (OSError, RuntimeError, ValueError):
        # A closed or lost stdin answers no.
        answer = ""
    if not answer or not sys.stdin.isatty():
        # No terminal echoed the answer and its newline.
        print(file=sys.stderr)
    return answer.strip().lower() in {"y", "yes"}


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


def helper_inventory(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    *,
    changes_by_helper: Optional[Dict[str, List[ChangeRecord]]] = None,
) -> HelperInventory:
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

    if changes_by_helper is None:
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
            if (
                not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                or node.name != name
                or node.lineno != lineno
            ):
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
    format_files: bool = False,
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
        total_changes, touched_paths = _stage_and_apply_rename_mappings(
            target,
            renames,
            dry_run,
            quiet=as_json,
            selected_helpers=helpers if restrict_selection else None,
            format_files=format_files,
        )
    except ValueError as error:
        if as_json:
            print(json.dumps({"applied": False, "dry_run": dry_run, "error": str(error)}))
        else:
            print(f"Error: {error}", file=sys.stderr)
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
    if not dry_run and not format_files and touched_paths:
        hint = _format_restore_hint(target, touched_paths)
        if hint is not None:
            print(hint)


def _apply_rename_mappings(
    target: Path,
    renames: Mapping[str, object],
    dry_run: bool,
    quiet: bool = False,
    *,
    selected_helpers: Optional[List[Tuple[Path, str, int, str]]] = None,
    format_files: bool = False,
) -> int:
    """Validate every mapping and stage the entire rename batch before any write."""
    count, _ = _stage_and_apply_rename_mappings(
        target,
        renames,
        dry_run,
        quiet=quiet,
        selected_helpers=selected_helpers,
        format_files=format_files,
    )
    return count


def _stage_and_apply_rename_mappings(
    target: Path,
    renames: Mapping[str, object],
    dry_run: bool,
    quiet: bool = False,
    *,
    selected_helpers: Optional[List[Tuple[Path, str, int, str]]] = None,
    format_files: bool = False,
) -> Tuple[int, List[Path]]:
    """Validate, optionally format with the project's formatter, and apply a rename batch."""
    from towel.changes import ChangePlan
    from towel.renaming import plan_rename_batch

    specifications: List[Tuple[str, str, Optional[Path]]] = []
    labels: List[str] = []
    for spec, new_name in renames.items():
        if not isinstance(new_name, str):
            raise ValueError(f"Invalid rename value for {spec}")
        file_filter = None
        name = spec
        if ":" in spec:
            relative, name = spec.rsplit(":", 1)
            file_filter = (target / relative).resolve()
            if target.resolve() not in file_filter.parents:
                raise ValueError(
                    f"File-qualified rename must name a file within target: {relative}"
                )
        if selected_helpers is None:
            specifications.append((name, new_name, file_filter))
            labels.append(spec)
        else:
            # ``helper``, ``helper.parameter``, or a class-private
            # ``Class.helper[.parameter]``: the helper is the first part that
            # names a selected one (or its already-applied new name).
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
                candidate_names = {new_name}
                if "." in spec:
                    parent_spec = spec.rsplit(".", 1)[0]
                    parent_target = renames.get(parent_spec)
                    if isinstance(parent_target, str):
                        candidate_names.add(parent_target)
                matched = {
                    path.resolve()
                    for path, helper, _, _ in selected_helpers
                    if helper in candidate_names
                    and (file_filter is None or path.resolve() == file_filter)
                }
            if not matched:
                selected_keys: List[str] = []
                for entry in helper_inventory(target, selected_helpers)["helpers"]:
                    selected_keys.append(entry["rename_key"])
                    for param in entry["parameters"]:
                        if param["name"].startswith("__param_"):
                            selected_keys.append(param["rename_key"])
                available = ", ".join(dict.fromkeys(selected_keys)) or "none"
                raise ValueError(
                    f"Unknown rename key {spec!r} (outside the selected helpers); "
                    f"available keys: {available}"
                )
            for path in sorted(matched):
                specifications.append((name, new_name, path))
                labels.append(spec)
    planned = plan_rename_batch(target, specifications, labels=labels)
    plan = planned.plan
    if format_files and plan.changes:
        before_map, after_map = _format_touched_sources(
            [(c.path, c.before, c.after) for c in plan.changes]
        )
        plan = ChangePlan.from_sources(before_map, after_map)
    if not dry_run:
        apply_changes(plan)
    if not quiet:
        already_set = set(planned.already_applied)
        for spec, new_name in renames.items():
            suffix = " (already applied)" if spec in already_set else ""
            print(f"  {spec} -> {new_name}{suffix}")
        if dry_run:
            diff = _format_plan_diff(target, [(c.path, c.before, c.after) for c in plan.changes])
            if diff:
                print("\n" + diff.rstrip("\n"))
    return planned.count, [c.path for c in plan.changes]


def _configured_project_formatter(path: Path) -> Optional[str]:
    """Return ``'ruff'`` or ``'black'`` when configured for ``path``, else ``None``."""
    from towel.formatting import _ruff_configuration
    from towel.project_layout import find_project_root, load_pyproject

    ruff_config = _ruff_configuration(path)
    absolute = path.resolve()
    folder = absolute if absolute.is_dir() else absolute.parent
    project_root = find_project_root(path).resolve()
    black_config: Optional[Path] = None
    for directory in (folder, *folder.parents):
        pyproject = load_pyproject(directory)
        tool = pyproject.get("tool", {}) if isinstance(pyproject, Mapping) else {}
        if isinstance(tool, Mapping) and isinstance(tool.get("black"), Mapping):
            black_config = directory / "pyproject.toml"
            break
        if directory == project_root:
            break
    if ruff_config is None and black_config is None:
        return None
    if ruff_config is not None and black_config is None:
        return "ruff"
    if black_config is not None and ruff_config is None:
        return "black"
    assert ruff_config is not None and black_config is not None
    if ruff_config.parent in black_config.parent.parents:
        return "black"
    if black_config.parent in ruff_config.parent.parents or ruff_config.name != "pyproject.toml":
        return "ruff"
    tool = load_pyproject(ruff_config.parent).get("tool", {})
    ruff_table = tool.get("ruff", {}) if isinstance(tool, Mapping) else {}
    if isinstance(ruff_table, Mapping) and isinstance(ruff_table.get("format"), Mapping):
        return "ruff"
    return "black"


def _format_touched_sources(
    changes: List[Tuple[Path, bytes, bytes]],
) -> Tuple[Dict[str, bytes], Dict[str, str]]:
    """Format touched files only when the project configures Ruff or Black."""
    from towel.formatting import (
        FormatterUnavailable,
        _RunFormatTools,
        _black_for_project,
        ruff_formatter,
    )
    from towel.source_text import decode_source

    before_map: Dict[str, bytes] = {}
    after_map: Dict[str, str] = {}
    tools = _RunFormatTools()
    try:
        for path, before_bytes, after_bytes in changes:
            before_map[str(path)] = before_bytes
            text = decode_source(after_bytes)
            kind = _configured_project_formatter(path)
            if kind == "ruff":
                try:
                    formatter = ruff_formatter(path)
                except FormatterUnavailable as error:
                    raise ValueError(
                        "Project configures Ruff, but 'ruff' is not installed; "
                        "install ruff or omit --format"
                    ) from error
                rendered = formatter(text)
                after_map[str(path)] = rendered + "\n" if rendered else text
            elif kind == "black":
                try:
                    choice = _black_for_project(path, "Black", tools)
                except ImportError as error:
                    raise ValueError(
                        "Project configures Black, but 'black' is not installed; "
                        "install black or omit --format"
                    ) from error
                if choice.tool is not None:
                    rendered = choice.tool(text)
                    after_map[str(path)] = rendered + "\n" if rendered else text
                else:
                    after_map[str(path)] = text
            else:
                after_map[str(path)] = text
    finally:
        for worker in list(tools._tools.values()):
            worker.close()
    return before_map, after_map


def _display_touched_path(target: Path, file_path: Path) -> str:
    resolved = file_path.resolve()
    cwd = Path.cwd().resolve()
    if resolved.is_relative_to(cwd):
        return resolved.relative_to(cwd).as_posix()
    resolved_target = target.resolve()
    if resolved.is_relative_to(resolved_target):
        return str(target / resolved.relative_to(resolved_target))
    return str(file_path)


def _format_restore_hint(target: Path, touched_paths: List[Path]) -> Optional[str]:
    """One-line command naming the project's configured formatter on touched files, if any."""
    import shlex

    by_tool: Dict[str, List[str]] = {}
    for path in touched_paths:
        kind = _configured_project_formatter(path)
        if kind is not None:
            by_tool.setdefault(kind, []).append(_display_touched_path(target, path))
    if not by_tool:
        return None
    commands: List[str] = []
    if "ruff" in by_tool:
        commands.append(shlex.join(("ruff", "format", *by_tool["ruff"])))
    if "black" in by_tool:
        commands.append(shlex.join(("black", *by_tool["black"])))
    return f"To restore formatting, run: {' && '.join(commands)}"


def _format_plan_diff(target: Path, changes: List[Tuple[Path, bytes, bytes]]) -> str:
    """Unified diff across staged file changes for human ``--preview`` review."""
    from difflib import unified_diff
    from towel.source_text import decode_source

    resolved_target = target.resolve()
    lines: List[str] = []
    for path, before_bytes, after_bytes in changes:
        try:
            relative = path.resolve().relative_to(resolved_target).as_posix()
        except ValueError:
            relative = path.name
        old = decode_source(before_bytes)
        new = decode_source(after_bytes)
        for line in unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"before/{relative}",
            tofile=f"after/{relative}",
        ):
            lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(lines)


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
    *,
    restrict_selection: bool = False,
    format_files: bool = False,
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
    print("across all files. Generated parameters use 'path/to/file.py:func_name.__param_0'.")
    print("A class-private helper, '__...' in a class, is named by its class")
    print("('path/to/file.py:Class.__func_name') and keeps a name starting with '__'.")
    print()
    print("Example:")
    print('  {"__extracted_func_1": "calculate_total"}')
    print('  {"src/utils.py:__extracted_func_2": "validate_input"}')
    print('  {"src/utils.py:__extracted_func_2.__param_0": "raw_value"}')
    print('  {"src/box.py:Box.__extracted_func_3": "__scaled_width"}')
    print()
    print("Paste the JSON response below, then press ENTER followed by Ctrl+D:")
    print()

    llm_response = sys.stdin.read().strip()
    if not llm_response:
        raise ValueError("No response provided. Aborted; no names were changed")

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
        raise ValueError(f"Error parsing JSON response: {e}. Supply a valid JSON object") from e

    if not isinstance(renames, dict):
        raise ValueError("Response must be a JSON object (dict)")

    print("\n" + "=" * 70)
    _banner("STEP 2: APPLYING RENAMINGS")

    try:
        total_changes, touched_paths = _stage_and_apply_rename_mappings(
            target,
            renames,
            dry_run,
            selected_helpers=helpers if restrict_selection else None,
            format_files=format_files,
        )
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)
    print(f"\n{'[PREVIEW] Would make' if dry_run else 'Applied'} {total_changes} change(s)")
    if not dry_run and not format_files and touched_paths:
        hint = _format_restore_hint(target, touched_paths)
        if hint is not None:
            print(hint)


def _generate_llm_prompt(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    llm_type: str,
) -> str:
    """Generate a prompt for an LLM to suggest better function and parameter names."""
    intro = (
        "I have extracted duplicate code into helper functions, and both the functions and their\n"
        "generated parameters still have placeholder names (__extracted_func_0, __param_0, etc.).\n"
        "\n"
        "Please review each helper, its generated parameter keys, and its call site, and suggest\n"
        "descriptive names for both the helper functions and their __param_N parameters.\n"
        "The new names should:\n"
        "- Be descriptive and indicate each function's purpose and each parameter's role\n"
        "- Follow Python naming conventions (lowercase with underscores)\n"
        "- Be concise and meaningful, avoiding generic names like 'helper', 'utility', or 'arg'\n"
        "- Name thunk parameters (called as name() inside the helper) for the value they yield\n"
        "\n"
        "Here are the extracted helpers:\n\n"
    )

    inventory_entries = {
        (entry["file"], entry["name"], entry["line"]): entry
        for entry in helper_inventory(target, helpers)["helpers"]
    }
    functions_section = ""
    for i, (file_path, func_name, lineno, source_preview) in enumerate(helpers, 1):
        rel_path = file_path.relative_to(target)
        entry = inventory_entries.get((str(rel_path), func_name, lineno))
        key = entry["rename_key"] if entry is not None else func_name
        helper_source = entry["source"] if entry is not None and entry["source"] else source_preview
        functions_section += f"\n{i}. {func_name} ({rel_path}:{lineno}), rename key {key!r}\n"
        if helper_source:
            functions_section += f"```python\n{helper_source}\n```\n"
        if entry is not None:
            generated_params = [
                param for param in entry["parameters"] if param["name"].startswith("__param_")
            ]
            if generated_params:
                functions_section += "Generated parameter rename keys:\n"
                for param in generated_params:
                    detail = ""
                    if param["kind"] == "thunk":
                        detail = f" (thunk, called as {param['name']}())"
                    elif param["kind"] == "lifted":
                        detail = " (callable)"
                    samples = [b["expression"] for b in param["bindings"] if b["expression"]][:2]
                    if samples:
                        detail += f"; passed {', '.join(samples)}"
                    functions_section += f"- {param['rename_key']!r}{detail}\n"
            if entry["calls"] and entry["calls"][0]["statement"]:
                call = entry["calls"][0]
                functions_section += (
                    f"Call site ({call['file']}:{call['line']}):\n"
                    f"```python\n{call['statement']}\n```\n"
                )

    output_format = (
        "\n"
        "Please provide your suggestions as a JSON object mapping each rename key to its new\n"
        "name, including both helper keys and generated __param_N parameter keys.\n"
        "\n"
        "Use the exact rename keys shown above (such as 'path.py:__extracted_func_0' for a\n"
        "helper and 'path.py:__extracted_func_0.__param_0' for one of its parameters).\n"
        "A helper whose rename key names a class ('path.py:Class.__name') is class-private:\n"
        "use that key, and give it a new name that also starts with two underscores (and does\n"
        "not end with two), such as '__scaled_width', because privacy keeps subclasses from\n"
        "overriding it.\n"
        "\n"
        "For example:\n"
        "\n"
        "```json\n"
        "{\n"
        '  "src/utils.py:__extracted_func_1": "calculate_total_price",\n'
        '  "src/utils.py:__extracted_func_1.__param_0": "tax_rate",\n'
        '  "src/validators.py:__extracted_func_1": "validate_email_format",\n'
        '  "__extracted_func_2": "format_date_string"\n'
        "}\n"
        "```\n"
        "\n"
        "Only include the JSON object in your response.\n"
    )

    return intro + functions_section + output_format


if __name__ == "__main__":
    main()
