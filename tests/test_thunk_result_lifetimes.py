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

"""Useful CLI extraction preserves factory-result temporary and local lifetimes."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import towel

_CASES = (
    (
        "subscript_assignment",
        "    d[key()] = value()\n",
        "    d[key().index] = value().item\n",
        "    same = (marker, marker)\n    return d, same\n",
    ),
    (
        "named_bindings",
        "    first = value()\n    second = key()\n",
        "    first = value().item\n    second = key().index\n",
        "    return d, first, second, marker\n",
    ),
    (
        "deleted_binding",
        "    first = value()\n    del first\n    second = key()\n",
        "    first = value().item\n    del first\n    second = key().index\n",
        "    return d, second, marker\n",
    ),
)

_MODES = (
    "ok",
    "value-error",
    "key-error",
    "value-attribute-error",
    "key-attribute-error",
    "store-error",
)

_DRIVER = """import gc
import json
import sys

sys.path.insert(0, sys.argv[1])
import case

EVENTS = []
MODE = sys.argv[2]

class Value:
    def __init__(self, label):
        self.label = label
        EVENTS.append(('new', label))
    def __del__(self):
        EVENTS.append(('del', self.label))
    @property
    def item(self):
        EVENTS.append(('item', self.label))
        if MODE == 'value-attribute-error':
            raise ArithmeticError('value attribute')
        return Value('item')
    @property
    def index(self):
        EVENTS.append(('index', self.label))
        if MODE == 'key-attribute-error':
            raise ArithmeticError('key attribute')
        return Value('index')

class Destination:
    def __setitem__(self, key, value):
        EVENTS.append(('store', key.label, value.label))
        if MODE == 'store-error':
            raise ArithmeticError('store')
    def __del__(self):
        EVENTS.append(('del', 'destination'))

def key():
    EVENTS.append(('key',))
    if MODE == 'key-error':
        raise ArithmeticError('key')
    return Value('key')

def value():
    EVENTS.append(('value',))
    if MODE == 'value-error':
        raise ArithmeticError('value')
    return Value('value')

case.key = key
case.value = value
for name in ('left', 'right'):
    EVENTS.clear()
    try:
        result = getattr(case, name)(Destination())
        del result
    except Exception as error:
        EVENTS.append(('exception', type(error).__name__, str(error)))
    gc.collect()
    print(json.dumps([name, EVENTS]))
"""


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TOWEL_WORKERS": "1",
    }


def _observe(driver: Path, project: Path, mode: str) -> tuple[int, str, str]:
    ran = subprocess.run(
        [sys.executable, "-B", str(driver), str(project), mode],
        cwd=driver.parent,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stderr == ""
    assert len(ran.stdout.splitlines()) == 2
    assert '"value"' in ran.stdout
    assert '"destination"' in ran.stdout
    return ran.returncode, ran.stdout, ran.stderr


@pytest.mark.parametrize("name,left,right,tail", _CASES, ids=[case[0] for case in _CASES])
def test_opaque_factory_results_keep_their_original_lifetimes(
    tmp_path: Path, name: str, left: str, right: str, tail: str
) -> None:
    project = tmp_path / name
    project.mkdir()
    output = tmp_path / (name + "-output")
    marker = "    marker = (1, 2, 3, 4, 5, 6, 7, 8)\n"
    source = "def left(d):\n" + left + marker + tail
    source += "\ndef right(d):\n" + right + marker + tail
    original = project / "case.py"
    original.write_text(source)
    configuration = project / "pyproject.toml"
    configuration.write_text(
        '[project]\nname="thunk-lifetime-control"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
    )
    original_configuration = configuration.read_bytes()
    runtime = Path(towel.__file__).resolve().parents[1]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(runtime)!r});"
        "sys.argv=['towel',*sys.argv[1:]];"
        "runpy.run_module('towel.cli',run_name='__main__')"
    )
    ran = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            bootstrap,
            "dry",
            str(project),
            str(output),
            "--no-interactive",
            "--min-lines",
            "3",
            "--progress",
            "none",
        ],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (
        sum(int(count) for count in re.findall(r"Applied (\d+) refactoring\(s\)", ran.stdout)) > 0
    )
    assert original.read_text() == source
    assert configuration.read_bytes() == original_configuration
    transformed = (output / "case.py").read_text()
    assert transformed != source
    driver = tmp_path / "observe.py"
    driver.write_text(_DRIVER)
    for mode in _MODES:
        before = _observe(driver, project, mode)
        after = _observe(driver, output, mode)
        assert after == before, (name, mode, before, after)
    module = ast.parse(transformed)
    helpers = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
    ]
    assert len(helpers) == 1
    helper = helpers[0]
    thunk_parameters = {argument.arg for argument in helper.args.posonlyargs}
    assert len(thunk_parameters) == 2
    deferred_calls = {
        node.func.id
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in thunk_parameters
        and not node.args
        and not node.keywords
    }
    assert deferred_calls == thunk_parameters
    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == helper.name
    ]
    assert len(calls) == 2
    assert all(all(isinstance(arg, ast.Lambda) for arg in call.args[:2]) for call in calls)
    assert not list(tmp_path.rglob("*.pyc"))
