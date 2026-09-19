"""One test per error path or rarely taken branch that the suite did not reach.

Each is a small, direct exercise of the code in question: the command
line's failure exit and its JSON contract for ``--rename-file``, pyright
output that is not JSON, the probe files removed at interpreter exit, the
formatter and import-sorter fallbacks, the prefix walk over the expression
kinds thunk inlining must model, the substituter's ``async with`` case,
binding context inside nested scopes, and ``TOWEL_WORKERS`` parsing.
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from tests.test_cli_integration import HELPER, invoke
from tests.test_thunk_inlining import _inline
from towel import formatting, type_inference
from towel.diagnostics import Settings
from towel.unification.binding_context import bound_variables_in_context
from towel.unification.extractor import ParameterSubstituter
from towel.unification.substitution import Substitution

# ---------------------------------------------------------------------------
# cli.py


def test_rename_helpers_failure_exits_nonzero_with_error_on_stderr(tmp_path: Path) -> None:
    """A rename file that cannot be opened is an OSError the dispatcher reports and exits on."""
    (tmp_path / "helpers.py").write_text(HELPER)
    result = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(tmp_path)])
    assert result.status == 1
    assert result.stderr.startswith("Error: ")
    assert (tmp_path / "helpers.py").read_text() == HELPER


def test_rename_file_json_contract_on_success_and_rejection(tmp_path: Path) -> None:
    source = tmp_path / "helpers.py"
    source.write_text(HELPER)
    mapping = tmp_path / "renames.json"
    command = ["rename-helpers", str(tmp_path), "--rename-file", str(mapping), "--json"]

    mapping.write_text(json.dumps({"__extracted_func_0": "double_value"}))
    dry = invoke([*command, "--preview"])
    assert dry.status == 0 and dry.stderr == ""
    payload = json.loads(dry.stdout)
    assert set(payload) == {"applied", "dry_run", "changes", "renames"}
    assert payload["applied"] is False and payload["dry_run"] is True and payload["changes"] > 0
    assert payload["renames"] == {"__extracted_func_0": "double_value"}
    assert source.read_text() == HELPER

    applied = invoke(command)
    assert applied.status == 0
    payload = json.loads(applied.stdout)
    assert payload["applied"] is True and payload["dry_run"] is False
    assert "double_value" in source.read_text() and "__extracted_func_0" not in source.read_text()

    mapping.write_text(json.dumps({"double_value": "class"}))
    rejected = invoke([*command, "--preview"])
    assert rejected.status == 2
    payload = json.loads(rejected.stdout)
    assert set(payload) == {"applied", "dry_run", "error"}
    assert payload["applied"] is False and payload["dry_run"] is True and payload["error"]
    assert "double_value" in source.read_text()


# ---------------------------------------------------------------------------
# type_inference.py


def _oracle_without_pyright() -> type_inference.PyrightOracle:
    oracle = type_inference.PyrightOracle.__new__(type_inference.PyrightOracle)
    oracle._command = ["pyright-stand-in"]
    return oracle


@pytest.mark.parametrize(
    "stdout, warning",
    [
        ("pyright crashed before writing anything\n", "produced no JSON"),
        ('{"generalDiagnostics": [}', "is not JSON"),
    ],
    ids=["no-braces", "malformed"],
)
def test_pyright_output_that_is_not_json_yields_no_diagnostics_and_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stdout: str,
    warning: str,
) -> None:
    module = tmp_path / "m.py"
    module.write_text("x = 1\n")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert Path(command[-1]).is_file(), "the probe exists while pyright runs"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level(logging.WARNING, logger="towel"):
        diagnostics = _oracle_without_pyright()._diagnostics(str(module), "x: int = 1\n")
    assert isinstance(diagnostics, type_inference.CheckFailure)
    assert commands[0][:2] == ["pyright-stand-in", "--outputjson"]
    assert any(warning in r.getMessage() for r in caplog.records), caplog.text
    assert not Path(commands[0][-1]).exists(), "the probe is removed after the run"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.py"]


def test_atexit_cleanup_removes_pending_probes_and_tolerates_missing_ones(tmp_path: Path) -> None:
    probe = tmp_path / "_towel_probe_m_abc.py"
    probe.write_text("")
    missing = tmp_path / "_towel_probe_m_gone.py"
    type_inference._PENDING_PROBES.update({probe, missing})
    type_inference._remove_pending_probes()
    assert not probe.exists()
    assert not type_inference._PENDING_PROBES & {probe, missing}


# ---------------------------------------------------------------------------
# formatting.py


def test_ruff_configured_but_not_installed_falls_back_to_black_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nline-length = 100\n[tool.ruff.lint]\nselect = ["I"]\n'
    )
    (tmp_path / "m.py").write_text("x = 1\n")
    monkeypatch.setattr(formatting, "_ruff_executable", lambda: None)
    choice = formatting.formatter_for_project(tmp_path / "m.py")
    assert choice.note == "Black; ruff is configured but not installed"
    assert choice.tool is not None and choice.tool("x=1\n") == "x = 1"
    sorter = formatting.import_sorter_for_project(tmp_path / "m.py")
    assert sorter.tool is None
    assert sorter.note == "ruff import sorting is configured but ruff is not installed"


@pytest.mark.parametrize("filename", ["setup.cfg", "tox.ini"])
def test_isort_section_in_ini_files_is_detected(tmp_path: Path, filename: str) -> None:
    (tmp_path / "m.py").write_text("x = 1\n")
    assert formatting.project_configures_isort(tmp_path / "m.py") is False
    (tmp_path / filename).write_text("[flake8]\nmax-line-length = 100\n")
    assert formatting.project_configures_isort(tmp_path / "m.py") is False
    (tmp_path / filename).write_text(
        "[flake8]\nmax-line-length = 100\n\n[isort]\nprofile = black\n"
    )
    assert formatting.project_configures_isort(tmp_path / "m.py") is True
    sorter = formatting.import_sorter_for_project(tmp_path / "m.py")
    assert sorter.note == "isort" and sorter.tool is not None
    assert (
        sorter.tool(str(tmp_path / "m.py"), "import os\nimport ast\n") == "import ast\nimport os\n"
    )


# ---------------------------------------------------------------------------
# thunk_inlining.py: the prefix walk over each modelled expression kind


@pytest.mark.parametrize(
    "source, expected",
    [
        ("def h(__param_0):\n    d = {'k': __param_0(), 'j': 1}\n    return d\n", {"__param_0"}),
        ("def h(__param_0):\n    d = {**__param_0()}\n    return d\n", {"__param_0"}),
        ("def h(__param_0):\n    s = f'value={__param_0()}!'\n    return s\n", {"__param_0"}),
        ("def h(__param_0):\n    xs = [x for x in __param_0()]\n    return xs\n", {"__param_0"}),
        # The element is evaluated per iteration, so it is not a leading thunk.
        ("def h(__param_0, xs):\n    ys = [__param_0() for x in xs]\n    return ys\n", set()),
        (
            "def h(__param_0):\n    if (y := __param_0()):\n        return y\n    return 0\n",
            {"__param_0"},
        ),
    ],
    ids=[
        "dict",
        "dict-unpack",
        "joinedstr",
        "comprehension-iter",
        "comprehension-element",
        "namedexpr",
    ],
)
def test_prefix_walk_models_each_expression_kind(source: str, expected: set[str]) -> None:
    inlined, rendered = _inline(source, "__param_0")
    assert inlined == expected, rendered
    assert ("__param_0()" in rendered) == (not expected)


# ---------------------------------------------------------------------------
# extractor.py: the substituter rebuilds ``async with`` like ``with``


def test_parameter_substituter_rebuilds_async_with() -> None:
    node = cast(
        ast.AsyncFunctionDef,
        ast.parse("async def f():\n    async with lock() as held:\n        use(held)\n").body[0],
    )
    statement = cast(ast.AsyncWith, node.body[0])
    rebuilt = ParameterSubstituter(Substitution(), [], {}).visit(statement)
    assert isinstance(rebuilt, ast.AsyncWith) and rebuilt is not statement
    # The substituter builds bare nodes; callers fix locations before rendering.
    assert ast.unparse(ast.copy_location(rebuilt, statement)) == ast.unparse(statement)
    assert rebuilt.items[0].optional_vars is statement.items[0].optional_vars


# ---------------------------------------------------------------------------
# binding_context.py: a nested def or class that contains the target


NESTED = (
    "def outer(a):\n"
    "    b = a\n"
    "    def helper(z):\n"
    "        return z\n"
    "    def inner(c):\n"
    "        d = c\n"
    "        return a + b + c + d\n"
    "    class Plain:\n"
    "        e = 1\n"
    "    class K:\n"
    "        e = 1\n"
    "        f = 2\n"
    "        g = a + b + e + f + g\n"
    "    return helper + inner + K + Plain + b\n"
)


def _bound(target: str) -> set[str]:
    outer = ast.parse(NESTED).body[0]
    return bound_variables_in_context(outer, ast.parse(target, mode="eval").body)


def test_nested_def_containing_the_target_starts_its_own_scope() -> None:
    # The nested def's parameter and assignment are bound around the target,
    # as is the enclosing parameter; the enclosing function's earlier
    # assignment ``b`` is not carried into the fresh scope.
    assert _bound("a + b + c + d") == {"a", "c", "d"}


def test_nested_class_containing_the_target_contributes_its_body_bindings() -> None:
    # ``g`` is being assigned, and assignment targets are recorded before
    # the value is visited, so it counts as bound alongside ``e`` and ``f``.
    assert _bound("a + b + e + f + g") == {"a", "e", "f", "g"}


def test_nested_scopes_without_the_target_bind_only_their_names() -> None:
    assert _bound("helper + inner + K + Plain + b") == {"helper", "inner", "K", "Plain", "b"}


# ---------------------------------------------------------------------------
# diagnostics.py: TOWEL_WORKERS


@pytest.mark.parametrize(
    "environ, workers",
    [
        ({}, None),
        ({"TOWEL_WORKERS": "4"}, 4),
        ({"TOWEL_WORKERS": "0"}, 1),
        ({"TOWEL_WORKERS": "-3"}, 1),
        ({"TOWEL_WORKERS": "many"}, 1),
        ({"TOWEL_WORKERS": ""}, 1),
    ],
    ids=["unset", "numeric", "zero", "negative", "non-numeric", "empty"],
)
def test_towel_workers_parsing(environ: dict[str, str], workers: int | None) -> None:
    assert Settings.from_environ(environ).workers == workers
