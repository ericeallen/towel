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

"""Actual extraction preserves captured comprehension cells on each native compiler."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import towel

from tests.differential.grammar import generate_case
from tests.differential.runner import Mode, run_case
from towel.type_inference import _configured_root

_FIXTURE = Path(__file__).parent / "fixtures/comprehension_captured_cell_original.py.txt"
_RUN = """
for label, function in (("f1", f1), ("f2", f2)):
    for arguments in ((0, [], {}, Box(0)), (1, [1, 2, 3], {"k": 1}, Box(1))):
        try:
            print("returned", label, function(*arguments))
        except Exception as error:
            print("raised", label, type(error).__name__, str(error))
        print("arguments-after", repr(arguments))
print("final-state", LOG, G)
"""


_VARIANTS = (
    ("lambda", "o"),
    ("lambda", "r"),
    ("generator", "o"),
    ("generator", "r"),
    ("outer-generator", "o"),
    ("lambda-default", "o"),
    ("lambda-default", "r"),
)
_CLI_VARIANTS = (
    *((kind, target, True) for kind, target in _VARIANTS),
    ("lambda-default-argument", "o", True),
    ("lambda-default-argument", "r", True),
    ("lambda-default", "r", False),
)


def _source(kind: str, target: str) -> str:
    if kind == "generator":
        expression = f"[next({target} + q for q in (1,)) for {target} in b]"
    elif kind == "outer-generator":
        expression = f"tuple((lambda: {target} + 1)() for {target} in b)"
    elif kind in ("lambda-default", "lambda-default-argument"):
        argument = "[1]" if kind == "lambda-default-argument" else ""
        expression = (
            f"(lambda ignored=[(lambda: {target} + 1)() for {target} in b]: ignored)"
            f"({argument})"
        )
    else:
        expression = f"[(lambda: {target} + 1)() for {target} in b]"
    return _FIXTURE.read_text().replace("[(lambda: o + 1)() for o in b]", expression)


def _environment() -> dict[str, str]:
    return {
        **{name: value for name, value in os.environ.items() if name != "PYTHONPATH"},
        "TOWEL_WORKERS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }


def _observe(executable: str, path: Path) -> tuple[int, str, str]:
    ran = subprocess.run([executable, "-B", str(path)], capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0 and not ran.stderr, ran.stderr
    assert ran.stdout.count("arguments-after") == 4
    return ran.returncode, ran.stdout, ran.stderr


@pytest.mark.parametrize("kind,target", _VARIANTS)
@pytest.mark.parametrize("cross", (False, True))
def test_actual_engine_preserves_captured_comprehension_observations(
    tmp_path: Path, kind: str, target: str, cross: bool
) -> None:
    generated = generate_case(2026100801792100000000000004010011, bindings=True, typed=False)
    original = _source(kind, target)
    case = replace(
        generated,
        files=tuple(
            (name, original if name == "pkg/m.py" else text) for name, text in generated.files
        ),
    )
    outcome = run_case(case, Mode(cross_module=cross, types=False), tmp_path)
    assert outcome.status in ("unchanged", "equivalent"), outcome.report()
    assert (tmp_path / "before/pkg/m.py").read_text() == original
    if target == "r" or kind == "outer-generator":
        assert outcome.applied > 0


@pytest.mark.parametrize("kind,target,configured", _CLI_VARIANTS)
def test_default_cli_keeps_native_errors_state_and_argument_effects(
    tmp_path: Path, kind: str, target: str, configured: bool
) -> None:
    original = tmp_path / "source"
    original.mkdir()
    source = _source(kind, target)
    (original / "case.py").write_text(source)
    config = '[project]\nname="captured-comprehension"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
    if configured:
        config += (
            '\n[tool.mypy]\npython_version="3.11"\nfiles=["case.py"]\n' "check_untyped_defs=true\n"
        )
    (original / "pyproject.toml").write_text(config)
    assert _configured_root(original / "case.py", "mypy") == (original if configured else None)
    output = tmp_path / "output"
    runtime = Path(towel.__file__).resolve().parents[1]
    bootstrap = (
        "import runpy,sys;" + f"sys.path.insert(0,{str(runtime)!r});"
        "sys.argv=['towel',*sys.argv[1:]];runpy.run_module('towel.cli',run_name='__main__')"
    )
    ran = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            bootstrap,
            "dry",
            str(original),
            str(output),
            "--no-interactive",
            "--progress",
            "none",
        ],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "cli.log").write_text(ran.stdout + ran.stderr)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (original / "case.py").read_text() == source
    assert (original / "pyproject.toml").read_text() == config
    transformed = (output / "case.py").read_text()
    applied = sum(map(int, re.findall(r"Applied (\d+) refactoring\(s\)", ran.stdout)))
    if kind == "lambda-default" and target == "r":
        # Mypy infers Never for the unused default parameter and cannot observe
        # the prospective helper return. The explicit-argument companion below
        # retains default evaluation while providing a checked useful extraction.
        assert applied == 0
        assert "the type checker does not look at the code it changes" in ran.stdout
        assert ast.dump(ast.parse(source)) == ast.dump(ast.parse(transformed))
    elif target == "r" or kind == "outer-generator" or kind == "lambda-default-argument":
        assert applied > 0
        assert ast.dump(ast.parse(source)) != ast.dump(ast.parse(transformed))
    before_path, after_path = tmp_path / "before.py", tmp_path / "after.py"
    before_path.write_text(source + _RUN)
    after_path.write_text(transformed + _RUN)
    choices = [sys.executable] + [
        executable
        for version in ("python3.11", "python3.12", "python3.13")
        if (executable := shutil.which(version)) is not None
    ]
    interpreters = tuple(dict.fromkeys(str(Path(executable).resolve()) for executable in choices))
    observations = []
    for executable in interpreters:
        before, after = _observe(executable, before_path), _observe(executable, after_path)
        observations.append({"executable": executable, "before": before, "after": after})
        (tmp_path / "observations.json").write_text(json.dumps(observations, indent=2) + "\n")
        assert before == after, (executable, before, after)
