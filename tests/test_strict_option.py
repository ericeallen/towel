# Copyright 2026 Eric Allen
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

"""The ``strict`` option reaches the engine from every place a run is configured.

Audit findings 1 and 2 (user-perspective audit of 1.949): the lifetime rules
decide what a first run looks like, so a run chooses between relaxed mode,
the default, and strict mode (docs/DECISIONS.md, "Relaxed mode is the
default; strict mode keeps exact lifetimes"). These tests pin the switch
itself; what each mode does is pinned in ``test_relaxed_mode.py``.
"""

import json
from pathlib import Path

import pytest

from towel import cli
from towel.api import UnificationRefactorEngine
from towel.type_inference import PyrightOracle
from towel.unification.rehearing import execution_context
from tests.test_cli_integration import DUPLICATES, invoke

FLAGS = ["--no-types", "--no-format", "--progress", "none"]


def _arguments(command: str, target: Path, *flags: str) -> list[str]:
    arguments = [command, str(target), *flags, *FLAGS]
    if command == "dry":
        arguments[2:2] = [str(target.with_name("out.py")), "--no-interactive"]
    return arguments


@pytest.mark.parametrize("command", ["dry", "preview"])
@pytest.mark.parametrize(
    "flags,strict", [((), False), (("--no-strict",), False), (("--strict",), True)]
)
def test_the_flag_becomes_an_option_of_the_command(
    tmp_path: Path, command: str, flags: tuple[str, ...], strict: bool
) -> None:
    """Relaxed is the default; ``--strict`` and ``--no-strict`` say so explicitly."""
    args = cli._build_parser().parse_args(_arguments(command, tmp_path / "example.py", *flags))
    options = (
        cli.DryOptions.from_namespace(args)
        if command == "dry"
        else cli.PreviewOptions.from_namespace(args)
    )
    assert options.strict is strict
    if isinstance(options, cli.PreviewOptions):
        # A complete preview is a private dry run with the same options.
        assert options.dry_options(str(tmp_path / "out")).strict is strict


def test_the_help_says_what_strict_mode_costs() -> None:
    """The flag's help text is the brief's, so a reader can choose without the guides."""
    for command in ("dry", "preview"):
        result = invoke([command, "--help"])
        text = " ".join(result.stdout.split())
        assert "--strict, --no-strict" in text
        assert (
            "Preserve exactly when objects are released and when names are looked up, as "
            "releases before this one did. Expect more blocks left alone and bookkeeping in "
            "callers." in text
        )


def test_the_engine_is_relaxed_unless_told_otherwise() -> None:
    assert UnificationRefactorEngine().strict is False
    assert UnificationRefactorEngine(strict=False).strict is False
    assert UnificationRefactorEngine(strict=True).strict is True


def test_a_rehearing_never_shares_a_verdict_between_modes(tmp_path: Path) -> None:
    """A proposal heard in one mode is not replayed as the other's: the mode is part of the context."""
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")

    engine = UnificationRefactorEngine()
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    relaxed = execution_context(engine)
    engine.strict = True
    strict = execution_context(engine)
    assert relaxed is not None and strict is not None
    assert relaxed != strict


@pytest.mark.parametrize("command", ["dry", "preview"])
@pytest.mark.parametrize("flags,mode", [((), "relaxed"), (("--strict",), "strict")])
def test_the_json_result_names_the_mode_that_ran(
    tmp_path: Path, command: str, flags: tuple[str, ...], mode: str
) -> None:
    """``mode`` is an additive field of schema version 1 (docs/COMPATIBILITY.md)."""
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    result = invoke(_arguments(command, source, "--json", *flags))
    assert result.status == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["mode"] == mode


@pytest.mark.parametrize("flags,mode", [((), "relaxed"), (("--strict",), "strict")])
def test_a_failed_run_still_names_its_mode(
    tmp_path: Path, flags: tuple[str, ...], mode: str
) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    result = invoke(_arguments("preview", loop, "--json", *flags))
    assert result.status == 1
    document = json.loads(result.stdout)
    assert document["status"] == "error"
    assert document["mode"] == mode


def test_the_schema_description_lists_the_mode_field() -> None:
    text = (Path(__file__).resolve().parents[1] / "docs" / "COMPATIBILITY.md").read_text()
    assert '| `mode` | `"relaxed"`, the default, or `"strict"`' in text
