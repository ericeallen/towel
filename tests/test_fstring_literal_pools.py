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

"""Ordinary identity/effect controls for caller-owned exact f-string fragments."""

from __future__ import annotations

import ast
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest
import towel

from towel.canonical_ast import canonical_dump
from towel.unification import compiler_literals as literals
from towel.unification.instantiation import _alpha_normalize, instantiation_mismatch
from towel.unification.bounded_cache import memoization_disabled

SUPPORT = """\
events = []
class Observer:
    def __init__(self, name, expected):
        self.name = name
        self.expected = expected
    def __format__(self, spec):
        events.append(('format', self.name, spec, spec is self.expected))
        return self.name
    def __str__(self):
        events.append(('str', self.name))
        return self.name
    def __repr__(self):
        events.append(('repr', self.name))
        return self.name

def width():
    events.append(('width',))
    return 5
"""


def _run(root: Path, program: str) -> str:
    ran = subprocess.run(
        [sys.executable, "-c", program],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout


@pytest.mark.parametrize(
    "expression,spec_is_original",
    [
        ("f'private literal!'", None),
        ("f'{obj:private spec!}'", True),
        ("f'prefix!{obj:private spec!}suffix!'", True),
        ("f'prefix!{obj!s}middle!{obj!r}suffix!{obj!a}'", None),
        ("f'prefix!{obj:private {width()} spec!}suffix!'", False),
        ("f'{obj:}'", None),
        ("f''", None),
    ],
    ids=[
        "single-chunk",
        "single-spec",
        "multiple-chunks",
        "conversions",
        "nested-width",
        "empty-spec",
        "empty-string",
    ],
)
def test_complete_cli_keeps_fstring_fragment_identity_and_format_effects(
    tmp_path: Path, expression: str, spec_is_original: bool | None
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='pkg'\nversion='0'\n")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "support.py").write_text(SUPPORT)
    for name, function, token in (("a", "first", "A"), ("b", "second", "B")):
        prefix = "import pkg.a\n" if name == "b" else ""
        (package / f"{name}.py").write_text(
            prefix
            + "from .support import Observer, width\n"
            + "EXPECTED = 'private spec!'\nLITERAL = 'private literal!'\n"
            + f"def {function}():\n"
            + f"    obj = Observer('{token}', 'private spec!')\n"
            + f"    text = {expression}\n"
            + "    changed = text.upper()\n"
            + "    return text, changed\n"
        )
    program = (
        "from pkg import a,b\nfrom pkg.support import events\n"
        "left=a.first();right=b.second()\n"
        "assert left[1]==left[0].upper() and right[1]==right[0].upper()\n"
        "print(left,right,events)\n"
    )
    if expression == "f'private literal!'":
        program += "assert left[0] is a.LITERAL and right[0] is b.LITERAL\nassert left[0] is not right[0]\n"
    if spec_is_original is not None:
        program += f"assert all(event[-1] is {spec_is_original} for event in events if event[0]=='format')\n"
    before = _run(tmp_path, program)
    original = {name: (package / f"{name}.py").read_bytes() for name in ("a", "b")}
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(tmp_path),
            str(tmp_path),
            "--no-interactive",
            "--progress",
            "none",
            "--no-types",
            "--no-format",
            "--cross-module",
        ],
        env={**os.environ, "PYTHONPATH": str(Path(towel.__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (
        sum((package / f"{name}.py").read_bytes() != data for name, data in original.items()) == 2
    ), ran.stdout
    assert _run(tmp_path, program) == before
    helpers = [
        node
        for name in ("a", "b")
        for node in ast.parse((package / f"{name}.py").read_text()).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert len(helpers) == 1
    assert len(helpers[0].args.posonlyargs + helpers[0].args.args) <= 5


def test_proof_normalization_erases_only_no_spec_exact_string_formatting() -> None:
    source = "return f\"{'private literal!'}\""
    tree = ast.parse(source)
    before = canonical_dump(tree)
    normalized = _alpha_normalize(tree)
    result = normalized.body[0]
    assert isinstance(result, ast.Return) and isinstance(result.value, ast.JoinedStr)
    assert isinstance(result.value.values[0], ast.Constant)
    assert result.value.values[0].value == "private literal!"
    assert canonical_dump(tree) == before
    for other in (
        "return f'{obj}'",
        "return f\"{'private literal!'!r}\"",
        "return f\"{'private literal!':>20}\"",
    ):
        tree = ast.parse(other)
        normalized = _alpha_normalize(tree)
        result = normalized.body[0]
        assert isinstance(result, ast.Return) and isinstance(result.value, ast.JoinedStr)
        assert isinstance(result.value.values[0], ast.FormattedValue)


def test_unknown_compiler_keeps_fstring_fragments_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 99))
    block = ast.parse("return f'private literal!'").body
    assert list(literals.common_literal_candidates(block)) == []
    tree = ast.parse("return f\"{'private literal!'}\"")
    assert canonical_dump(_alpha_normalize(tree)) == canonical_dump(copy.deepcopy(tree))


def test_capability_changes_do_not_reuse_a_different_format_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = ast.parse("def helper(carrier):\n return f'{carrier[0]}'").body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("return helper(('private literal!',))").body[0]
    original = ast.parse("return f'private literal!'").body
    verdict = instantiation_mismatch(
        helper,
        call,
        original,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    assert verdict is None
    monkeypatch.setattr(sys, "version_info", (3, 99))
    cached = instantiation_mismatch(
        helper,
        call,
        original,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    with memoization_disabled():
        uncached = instantiation_mismatch(
            helper,
            call,
            original,
            {},
            {},
            site_function_names=frozenset(),
            preamble_length=0,
            returns_variables=False,
        )
    assert cached == uncached and cached is not None
    # The expected binding-form memo likewise carries this capability. Here
    # both unsupported sides contain a FormattedValue and must still agree.
    original = ast.parse("return f\"{'private literal!'}\"").body
    assert (
        instantiation_mismatch(
            helper,
            call,
            original,
            {},
            {},
            site_function_names=frozenset(),
            preamble_length=0,
            returns_variables=False,
        )
        is None
    )
    monkeypatch.undo()
    assert (
        instantiation_mismatch(
            helper,
            call,
            original,
            {},
            {},
            site_function_names=frozenset(),
            preamble_length=0,
            returns_variables=False,
        )
        is None
    )
    monkeypatch.setattr(sys, "version_info", (3, 99))
    cached = instantiation_mismatch(
        helper,
        call,
        original,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    with memoization_disabled():
        uncached = instantiation_mismatch(
            helper,
            call,
            original,
            {},
            {},
            site_function_names=frozenset(),
            preamble_length=0,
            returns_variables=False,
        )
    assert cached == uncached is None
