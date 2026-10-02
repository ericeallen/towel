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

"""Ordinary module writes retain each caller's builtin lookup after extraction."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Tuple

import pytest

from tests.test_builtins_across_modules import _project, _run, _write
from tests.test_parameterize_builtins import _refactor

WRITES = {
    "direct": "exports.len = replacement\n",
    "parameter": "def install(module):\n    module.len = replacement\ninstall(exports)\n",
    "container": "modules = [exports]\nmodules[0].len = replacement\n",
    "returned": "def choose():\n    return exports\nchoose().len = replacement\n",
    "conditional": "chosen = exports if True else reports\nchosen.len = replacement\n",
    "multiple_aliases": "left = right = exports\nright.len = replacement\n",
    "loop_target": "for exports.len in [replacement]:\n    pass\n",
    "comprehension_target": "unused = [None for exports.len in [replacement]]\n",
    "with_target": (
        "class Context:\n"
        "    def __enter__(self):\n        return replacement\n"
        "    def __exit__(self, *args):\n        return False\n"
        "with Context() as exports.len:\n    pass\n"
    ),
    "unicode_normalized": "exports.ℓen = replacement\n",
}


@pytest.mark.parametrize("parameterize", [False, True])
@pytest.mark.parametrize("case", sorted(WRITES))
def test_builtin_writes_through_ordinary_receivers_preserve_execution(
    tmp_path: Path, case: str, parameterize: bool
) -> None:
    source = (
        "from pkg import exports, reports\n"
        "def replacement(value):\n    return 100\n"
        + WRITES[case]
        + "print(exports.export_size([1, 2], 'ab'), reports.report_size([1, 2], 'ab'))\n"
    )
    before, after = tmp_path / "before", tmp_path / "after"
    files = _project(extra={"check.py": source})
    for root in (before, after):
        _write(root, files)
    expected = _run(before, "check.py")
    assert "10001 5" in expected
    applied, _ = _refactor(after / "pkg", parameterize)
    assert bool(applied) is parameterize
    assert _run(after, "check.py") == expected


def test_setup_module_does_not_hide_a_driver_that_rebinds_a_builtin(tmp_path: Path) -> None:
    files = _project(
        extra={
            "pkg/setup.py": "def configure():\n    return 1\n",
            "check.py": "from pkg import exports\n"
            "exports.len = lambda value: 100\n"
            "print(exports.export_size([1, 2], 'ab'))\n",
        }
    )
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, files)
    applied, _ = _refactor(after / "pkg", False)
    assert applied == 0
    assert _run(after, "check.py") == _run(before, "check.py")


def test_normalized_identifiers_use_the_declared_source_encoding(tmp_path: Path) -> None:
    files = _project()
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, files)
        (root / "check.py").write_bytes(
            (
                "# coding: shift_jis\n"
                "from pkg import exports\n"
                "exports.ｌｅｎ = lambda value: 100\n"
                "print(exports.export_size([1, 2], 'ab'))\n"
            ).encode("shift_jis")
        )
    applied, _ = _refactor(after / "pkg", False)
    assert applied == 0
    assert _run(after, "check.py") == _run(before, "check.py")


@pytest.mark.parametrize("parameterize", [False, True])
@pytest.mark.parametrize("names", [("len", "len"), ("measure", "measure"), ("measure", "compute")])
@pytest.mark.parametrize("operation", ["rebind", "delete", "unbound"])
def test_external_callee_can_change_lookup_between_reads(
    tmp_path: Path, parameterize: bool, names: Tuple[str, str], operation: str
) -> None:
    """A dependency can import and modify the caller without receiving it as an argument."""
    name, other_name = names
    body = (
        f"    first = {name}(values)\n"
        "    callback()\n"
        f"    second = {name}(values)\n"
        "    result = first * 1000 + second\n"
        "    return result * FACTOR\n"
    )
    definition = f"def {name}(values):\n    return 3\n"
    prelude = definition if name == "measure" or operation == "delete" else ""
    action = (
        f"    b.{other_name} = lambda values: 101\n"
        if operation == "rebind"
        else (f"    del b.{other_name}\n" if prelude else "    del builtins.len\n")
    )
    outside = (
        "import builtins\noriginal_len = builtins.len\n"
        "def install():\n    from pkg import b\n" + action + "\n"
        "def restore():\n    builtins.len = original_len\n"
    )
    files = {
        "pyproject.toml": '[project]\nname="probe"\nversion="0"\n',
        "pkg/__init__.py": "",
        "pkg/a.py": prelude + "def first(values, callback):\n" + body.replace("FACTOR", "5"),
        "pkg/b.py": "from pkg import a\n"
        + prelude.replace(name, other_name)
        + "def second(values, callback):\n"
        + body.replace("FACTOR", "7").replace(name, other_name),
        "check.py": "from pkg import a, b\nfrom outside import install, restore\n"
        "first = a.first([1, 3], lambda: None)\n"
        "try:\n    second = b.second([2, 4], install)\n"
        "except NameError:\n    second = 'NameError'\n"
        "finally:\n    restore()\n"
        "print(first, second)\n",
    }
    before, after, vendor = tmp_path / "before", tmp_path / "after", tmp_path / "vendor"
    _write(vendor, {"outside.py": outside})
    for root in (before, after):
        _write(root, files)
    expected = _run(before, "check.py", pythonpath=str(vendor))
    applied, _ = _refactor(after / "pkg", parameterize)
    assert bool(applied) is (parameterize or name != "len")
    assert _run(after, "check.py", pythonpath=str(vendor)) == expected


def test_a_single_leading_builtin_lookup_can_be_passed_eagerly(tmp_path: Path) -> None:
    body = (
        "    size = len(values) + 1\n"
        "    result = size * 2\n"
        "    total = result + 3\n"
        "    return total * FACTOR\n"
    )
    files = {
        "pyproject.toml": '[project]\nname="probe"\nversion="0"\n',
        "pkg/__init__.py": "",
        "pkg/a.py": "def first(values):\n" + body.replace("FACTOR", "5"),
        "pkg/b.py": "from pkg import a\ndef second(values):\n" + body.replace("FACTOR", "7"),
        "check.py": "from pkg import a, b\nprint(a.first([1, 3]), b.second([2, 4]))\n",
    }
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, files)
    applied, _ = _refactor(after / "pkg", True)
    assert applied > 0
    assert _run(after, "check.py") == _run(before, "check.py") == "45 63\n"
    assert not any(
        isinstance(node, ast.Lambda)
        for path in (after / "pkg").glob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
    )


@pytest.mark.parametrize("parameterize", [False, True])
@pytest.mark.parametrize("clustered", [False, True])
@pytest.mark.parametrize("name", ["len", "measure"])
def test_mixed_local_and_builtin_sites_keep_late_lookup(
    tmp_path: Path, parameterize: bool, clustered: bool, name: str
) -> None:
    body = (
        "    first = len(values)\n"
        "    callback()\n"
        "    second = len(values)\n"
        "    result = first * 1000 + second\n"
        "    return result * FACTOR\n"
    )
    source = "def first(values, callback, len=lambda value: 3):\n" + body.replace("FACTOR", "5")
    if clustered:
        source += "\ndef other(values, callback, len=lambda value: 4):\n" + body.replace(
            "FACTOR", "6"
        )
    source += "\ndef second(values, callback):\n" + body.replace("FACTOR", "7")
    driver = (
        "import m\ndef install():\n    m.len = lambda values: 101\n"
        "print(m.first([1, 3], lambda: None), m.second([2, 4], install))\n"
    )
    if name != "len":
        source = "def measure(values):\n    return 2\n" + source.replace("len", name)
        driver = driver.replace("len", name)
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": source, "check.py": driver})
    expected = _run(before, "check.py")
    applied, _ = _refactor(after / "m.py", parameterize)
    assert bool(applied) is (parameterize or clustered or name != "len")
    assert _run(after, "check.py") == expected


@pytest.mark.parametrize("parameterize", [False, True])
@pytest.mark.parametrize("name", ["len", "measure"])
@pytest.mark.parametrize(
    "comprehension",
    [
        "[None for len in ()]",
        "{len for len in ()}",
        "{len: None for len in ()}",
        "(len for len in ())",
    ],
)
def test_comprehension_target_does_not_hide_a_clustered_module_lookup(
    tmp_path: Path, parameterize: bool, name: str, comprehension: str
) -> None:
    body = (
        "    first = len(values)\n"
        "    callback()\n"
        "    second = len(values)\n"
        "    result = first * 1000 + second\n"
        "    return result * FACTOR\n"
    )
    # The first pair can share an eager local argument. The third occurrence
    # must still look in its module after callback() changes that binding.
    source = "def first(values, callback, len=lambda value: 3):\n" + body.replace("FACTOR", "5")
    source += "\ndef other(values, callback, len=lambda value: 4):\n" + body.replace("FACTOR", "6")
    source += "\ndef second(values, callback):\n" f"    unused = {comprehension}\n" + body.replace(
        "FACTOR", "7"
    )
    driver = (
        "import m\ndef install():\n    m.len = lambda values: 101\n"
        "print(m.first([1, 3], lambda: None), m.other([1, 3], lambda: None), "
        "m.second([2, 4], install))\n"
    )
    if name != "len":
        source = "def measure(values):\n    return 2\n" + source.replace("len", name)
        driver = driver.replace("len", name)
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": source, "check.py": driver})
    assert _run(before, "check.py") == "15015 24024 14707\n"
    applied, _ = _refactor(after / "m.py", parameterize)
    assert applied > 0, "the two occurrences with local arguments can still share"
    assert _run(after, "check.py") == _run(before, "check.py")
