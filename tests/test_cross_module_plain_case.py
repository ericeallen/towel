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

"""``--cross-module`` shares the plain case, and says why when it shares nothing.

Audit finding 2 (engine map F; scenario 37, probes p6 and p7): with
``--cross-module`` Towel shared nothing on any designed case or on three real
projects, and said so one reason code per attempt. Since 2.0:

- when it shares nothing across modules, one message names, for each pair of
  modules, the condition that failed, and the one remedy that exists;
- a module name that every participating module binds by the same absolute
  import (``import re`` in both) is read bare in the helper, in relaxed mode;
- in relaxed mode a builtin is read bare in the helper unless a participating
  module binds that name (docs/design/DECISIONS.md, "Relaxed mode reads a
  builtin bare across modules");
- in relaxed mode an immutable literal moves into the shared helper with the
  code around it, as it does within one module.

Strict mode keeps 1.949's refusals and ``--parameterize-builtins``.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from tests.test_cli_integration import invoke
from tests.test_callback_parameters import FIXTURES, run_script

pytestmark = pytest.mark.relaxed_mode


def _scenario(tmp_path: Path, fixture: str) -> Tuple[Path, Path]:
    case = tmp_path / Path(fixture).name
    for path in sorted((FIXTURES / fixture).rglob("*.py.txt")):
        target = case / path.relative_to(FIXTURES / fixture).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return case / "src", case / "out"


def _dry(source: Path, output: Path, *options: str) -> Tuple[str, str]:
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--cross-module",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            *options,
        ]
    )
    assert result.status == 0, result.stderr
    generated = "\n".join(
        f"# {path.relative_to(output)}\n{path.read_text()}" for path in sorted(output.rglob("*.py"))
    )
    return result.stdout, generated


def _write(root: Path, files: Dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _helpers(generated: str) -> List[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(generated))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]


# -- One message ---------------------------------------------------------------


def test_sharing_nothing_names_each_failed_condition_and_the_remedy(tmp_path: Path) -> None:
    """Probe p6: two sibling modules of a package, neither importing the other."""
    source, output = _scenario(tmp_path, "reach/p6_xmod")
    printed, _ = _dry(source, output)
    assert printed.count("--cross-module shared nothing between modules") == 1
    message = printed[printed.index("--cross-module shared nothing between modules") :]
    assert "pkg/contacts.py and pkg/labels.py" in message
    assert "would replace the attribute contacts of package pkg" in message or (
        "would replace the attribute labels of package pkg" in message
    )
    assert "already imports" in message and "at its top" in message
    # One line per pair of modules and condition, not one per pair of blocks.
    assert message.count("pkg/contacts.py and pkg/labels.py") == 1


def test_the_remedy_works(tmp_path: Path) -> None:
    """With ``import pkg.contacts`` at the top of labels.py, p6 shares its loop."""
    source, output = _scenario(tmp_path, "reach/p6_xmod")
    labels = source / "pkg" / "labels.py"
    labels.write_text(
        labels.read_text().replace('"""Labels."""\n', '"""Labels."""\n\nimport pkg.contacts\n')
    )
    printed, generated = _dry(source, output)
    assert "shared nothing" not in printed
    assert len(_helpers(generated)) == 1
    driver = source.parent / "driver.py"
    assert run_script(driver, str(output)) == run_script(driver, str(source))
