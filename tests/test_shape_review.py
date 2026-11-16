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

"""Executable regressions from the independent shape round-two review."""

from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_cli_integration import CliResult, invoke


def dry(source: Path, output: Path, strict: bool, *options: str) -> CliResult:
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--strict" if strict else "--no-strict",
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
            *options,
        ]
    )
    assert result.status == 0, result
    return result


def observe(root: Path, driver: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("strict", [False, True])
def test_return_permutation_preserves_strict_finalizer_order(tmp_path: Path, strict: bool) -> None:
    """Review 3: redirect return permutations must preserve strict frame-slot order."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "mod.py").write_text("""events = []
class Noisy:
    def __init__(self, label): self.label = label
    def __del__(self): events.append(f"del {self.label}")

def total():
    first = Noisy("1")
    second = Noisy("2")
    events.append("built")
    return second, first

def report():
    first = Noisy("1")
    second = Noisy("2")
    events.append("built")
    events.append("after block")
    return 0
""")
    driver = "import mod; mod.report(); print(mod.events)"
    before = observe(source, driver)
    assert before == "['built', 'after block', 'del 1', 'del 2']\n"
    output = tmp_path / "out"
    dry(source, output, strict, "--complete-scope")
    after = observe(output, driver)
    if strict:
        assert after == before
    else:
        assert "built" in after and "after block" in after


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("indirect", [False, True])
def test_forward_redirect_is_bound_before_import_time_calls(
    tmp_path: Path, strict: bool, indirect: bool
) -> None:
    """Review 1: a caller may run before a later equivalent function is defined."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    boot = "BOOT = report([1, 2], [])"
    if indirect:
        boot = "def boot():\n    return report([1, 2], [])\nBOOT = boot()"
    (source / "mod.py").write_text(
        """def report(order, log):
    log.append("report")
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)
    return tuple(log)

"""
        + boot
        + """

def total(order, log):
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)
"""
    )
    before = observe(source, "import mod; print(mod.BOOT)")
    assert before == "('report', 2, 6)\n"
    output = tmp_path / "out"
    dry(source, output, strict)
    assert observe(output, "import mod; print(mod.BOOT)") == before


REDIRECT_SOURCE = """def total(order, log):
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)

def report(order):
    log = []
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)
    return f"amount={log[-1]}"
"""


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "mutation",
    [
        "with mock.patch(target='original.total', new=replacement):\n    print(original.report([1, 2]))",
        "with mock.patch.object(target=original, attribute='total', new=replacement):\n    print(original.report([1, 2]))",
        "with mock.patch(f'{original.__name__}.total', replacement):\n    print(original.report([1, 2]))",
        "target = 'original.total'\nwith mock.patch(target, replacement):\n    print(original.report([1, 2]))",
        "attr = 'total'\nwith mock.patch.object(original, attr, replacement):\n    print(original.report([1, 2]))",
        "with mock.patch.dict(original.__dict__, {'total': replacement}):\n    print(original.report([1, 2]))",
        "with mock.patch.multiple(original, **{'total': replacement}):\n    print(original.report([1, 2]))",
        "ns = original.__dict__\nns['total'] = replacement\nprint(original.report([1, 2]))",
        "args = (original, 'total', replacement)\nsetattr(*args)\nprint(original.report([1, 2]))",
        "original.__setattr__('total', replacement)\nprint(original.report([1, 2]))",
        "p = mock.patch\nwith p('original.total', replacement):\n    print(original.report([1, 2]))",
        "p = setattr\np(original, 'total', replacement)\nprint(original.report([1, 2]))",
        "mp = pytest.MonkeyPatch()\nmp.setattr(target=original, name='total', value=replacement)\nprint(original.report([1, 2]))\nmp.undo()",
        "mp = pytest.MonkeyPatch()\nmp.delattr(target='original.total')\nprint(original.report([1, 2]))\nmp.undo()",
    ],
)
def test_patch_forms_preserve_independent_callers(
    tmp_path: Path, strict: bool, mutation: str
) -> None:
    """Review 2: patching an original must not change its former duplicate's behavior."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "original.py").write_text(REDIRECT_SOURCE)
    (source / "client.py").write_text(
        "import unittest.mock as mock, original, pytest\n"
        "replacement = lambda order, log: log.append(0)\n" + mutation + "\n"
    )
    before = observe(source, "import client")
    assert before == "amount=6\n"
    output = tmp_path / "out"
    dry(source, output, strict)
    assert observe(output, "import client") == before
