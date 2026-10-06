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

"""Extraction coverage across standard-library bases, without a runtime base allowlist."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from tests.stdlib_bases import STDLIB_BASES
from tests.test_helpers import method_helper_calls, refactor_to_fixed_point_silently
from tests.test_method_host_machinery import _run

_METHODS = """
    v = 10

    def first(self, n):
        print("first", n)
        total = self.v + n
        total = total * 2
        print("done", total)
        return total

    def second(self, n):
        print("second", n)
        total = self.v + n
        total = total * 2
        print("done", total)
        return total
"""


def _source(origin: str, suffix: str = "", members: str = "") -> str:
    module, _, name = origin.rpartition(".")
    return (
        f"from {module} import {name} as Base\n\nclass Box(Base{suffix}):\n"
        + textwrap.indent(textwrap.dedent(_METHODS).lstrip("\n"), "    ")
        + textwrap.indent(textwrap.dedent(members).lstrip("\n"), "    ")
    )


def _refactor(path: Path, source: str) -> str:
    path.write_text(source)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert applied > 0
    compile(final, str(path), "exec")
    klass = next(node for node in ast.parse(final).body if isinstance(node, ast.ClassDef))
    helpers = [
        node
        for node in klass.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
    ]
    assert helpers
    assert method_helper_calls(final)
    path.write_text(final)
    return final


@pytest.mark.parametrize(
    "origin", sorted(origin for base in STDLIB_BASES for origin in base.origins)
)
def test_library_base_allows_private_extraction(tmp_path: Path, origin: str) -> None:
    _refactor(tmp_path / "box.py", _source(origin))


@pytest.mark.parametrize(
    "origin", sorted(origin for base in STDLIB_BASES if base.subscripted for origin in base.origins)
)
def test_subscripted_library_base_allows_private_extraction(tmp_path: Path, origin: str) -> None:
    _refactor(tmp_path / "box.py", _source(origin, "[int]"))


@pytest.mark.parametrize(
    "origin, members, driver",
    [
        (
            "unittest.TestCase",
            "def test_first(self):\n    self.assertEqual(self.first(1), 22)\n"
            "def test_second(self):\n    self.assertEqual(self.second(2), 24)\n",
            "import unittest\nfrom box import Box\n"
            "suite = unittest.defaultTestLoader.loadTestsFromTestCase(Box)\n"
            "result = unittest.TestResult()\nsuite.run(result)\n"
            "print(result.testsRun, result.wasSuccessful())\n",
        ),
        (
            "collections.UserDict",
            "",
            "from box import Box\nbox = Box({'a': 1})\n"
            "print(box.first(1), box.second(2), box['a'])\n",
        ),
        (
            "contextlib.AbstractContextManager",
            "def __exit__(self, *args):\n    return False\n",
            "from box import Box\nwith Box() as box:\n    print(box.first(1), box.second(2))\n",
        ),
        (
            "asyncio.Protocol",
            "",
            "from box import Box\nbox = Box()\nprint(box.first(1), box.second(2))\n",
        ),
    ],
)
def test_library_base_execution_and_discovery_are_preserved(
    tmp_path: Path, origin: str, members: str, driver: str
) -> None:
    source = _source(origin, members=members)
    path = tmp_path / "box.py"
    path.write_text(source)
    before = _run(tmp_path, driver)
    assert before.endswith("\n|"), before
    _refactor(path, source)
    assert _run(tmp_path, driver) == before
