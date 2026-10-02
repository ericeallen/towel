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

"""A relative module named __future__ has ordinary runtime import semantics."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from towel.reachability import probe_plan
from towel.type_baseline import import_probes
from towel.unification.annotations import evaluated_syntax, written_for_python
from towel.unification.import_graph import ImportTimeCode
from towel.unification.scope_analyzer import ScopeAnalyzer


@pytest.mark.parametrize("relative", [False, True])
def test_relative_future_annotations_execute_and_remain_free(
    tmp_path: Path, relative: bool
) -> None:
    prefix = "." if relative else ""
    source = (
        f"from {prefix}__future__ import annotations\n"
        "events = []\n"
        "def effect():\n"
        "    events.append('effect')\n"
        "    return int\n"
        "def target(value: effect()):\n"
        "    return value\n"
        "print(events)\n"
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__future__.py").write_text("annotations = 0\n")
    (package / "sample.py").write_text(source)
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import pkg.sample",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == ("['effect']\n" if relative else "[]\n")
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    assert ("effect" in analyzer.free_variables([tree.body[3]])) is relative
    assert ImportTimeCode(source).statements()[3] is relative


@pytest.mark.parametrize("relative", [False, True])
def test_relative_future_does_not_exempt_generated_annotations_from_python_support(
    relative: bool,
) -> None:
    prefix = "." if relative else ""
    host = ast.parse(
        f"from {prefix}__future__ import annotations\n"
        "def existing(value: int | None): return value\n"
    )
    helper = ast.parse("def helper(value: int | None): return value\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    written = written_for_python(helper, host, (3, 9))
    annotation = written.args.args[0].annotation
    assert isinstance(annotation, ast.Constant) is relative
    if relative:
        assert evaluated_syntax(host) == (3, 10)


@pytest.mark.parametrize("relative", [False, True])
def test_relative_future_is_an_ordinary_checker_probe_site(relative: bool) -> None:
    prefix = "." if relative else ""
    source = f"from {prefix}__future__ import annotations\nvalue = 1\n"
    plan = probe_plan(source)
    assert plan is not None
    assert ((1, 0) in plan.sites) is relative
    probes = import_probes("/project/pkg/sample.py", source)
    assert (probes is not None) is relative
    if probes is not None:
        assert any(
            question.subject == '"annotations" imported from ".__future__"'
            for question in probes.questions
        )
