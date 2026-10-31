# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Checker-only imports must stay inert when an ordinary program changes typing's flag."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_type_checking_imports import _package
from towel.import_model import _scan_tree
from towel.runtime_guards import module_false_guards
from towel.unification.refactor_engine import UnificationRefactorEngine


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments], cwd=root, capture_output=True, text=True, timeout=600
    )


def _checker(root: Path, checker: str) -> subprocess.CompletedProcess[str]:
    arguments = ("--strict", "--no-incremental", "consumer.py") if checker == "mypy" else (".",)
    return _run(root, "-I", "-m", checker, *arguments)


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_generated_false_guard_keeps_real_checker_precision(tmp_path: Path, checker: str) -> None:
    if importlib.util.find_spec(checker) is None:
        pytest.skip(f"{checker} absent")
    (tmp_path / "other.py").write_text("class Thing:\n    pass\n")
    (tmp_path / "pyrightconfig.json").write_text(
        '{"typeCheckingMode": "strict", "include": ["consumer.py"]}\n'
    )
    # Pyright hides imports under literal False; the writer must not join it.
    lines = [
        "if False:\n",
        "    pass\n",
        "def identity(value: 'Thing') -> 'Thing':\n",
        "    return value\n",
    ]
    bound = UnificationRefactorEngine()._ensure_type_checking_import(lines, "other", "Thing")
    source = "".join(lines).replace("'Thing'", repr(bound))
    consumer = tmp_path / "consumer.py"
    consumer.write_text(source)
    accepted = _checker(tmp_path, checker)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    consumer.write_text(source + "\nidentity(42)\n")
    rejected = _checker(tmp_path, checker)
    assert rejected.returncode != 0 and "argument" in rejected.stdout.lower(), rejected.stdout
    consumer.write_text(source)
    imported = _run(
        tmp_path, "-c", "import typing; typing.TYPE_CHECKING = True; import consumer; print('ok')"
    )
    assert (imported.returncode, imported.stdout) == (0, "ok\n"), imported.stderr


def test_typed_cli_preserves_imports_with_type_checking_true(tmp_path: Path) -> None:
    checker = "mypy"
    if importlib.util.find_spec(checker) is None:
        pytest.skip(f"{checker} absent")
    package = _package(tmp_path)
    # This test needs a real preexisting host load to reach annotation
    # generation; importing only its package does not establish one.
    borrower_path = package / "beta.py"
    borrower_path.write_text(
        borrower_path.read_text(encoding="utf-8").replace("import pkg\n", "import pkg.alpha\n"),
        encoding="utf-8",
    )
    script = "import typing\ntyping.TYPE_CHECKING = True\nfrom pkg.beta import Beta\nprint(Beta().render(6))\n"
    before = _run(tmp_path, "-c", script)
    assert (before.returncode, before.stdout) == (0, ".....B\n"), before.stderr
    transformed = _run(
        tmp_path,
        "-m",
        "towel.cli",
        "dry",
        str(package),
        str(package),
        "--cross-module",
        "--parameterize-builtins",
        "--no-interactive",
        "--no-format",
        "--progress",
        "none",
        "--min-lines",
        "3",
    )
    assert transformed.returncode == 0, transformed.stdout + transformed.stderr
    host = (package / "alpha.py").read_text()
    borrower = (package / "beta.py").read_text()
    assert "extracted_func" in host and "extracted_func" in borrower, transformed.stdout
    assert "if 0 > 1:" in host, host
    after = _run(tmp_path, "-c", script)
    assert (after.returncode, after.stdout, after.stderr) == (
        before.returncode,
        before.stdout,
        before.stderr,
    )
    checked = _run(
        tmp_path,
        "-I",
        "-m",
        checker,
        *(("--strict", "--no-incremental", "pkg") if checker == "mypy" else (".",)),
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_existing_imported_guard_keeps_its_runtime_effects(tmp_path: Path) -> None:
    lines = [
        "import typing\n",
        "typing.TYPE_CHECKING = True\n",
        "if typing.TYPE_CHECKING:\n",
        "    result = 'ran'\n",
    ]
    UnificationRefactorEngine()._ensure_type_checking_import(lines, "missing_module", "Thing")
    ran = _run(tmp_path, "-c", "".join(lines) + "print(result)\n")
    assert (ran.returncode, ran.stdout) == (0, "ran\n"), ran.stderr
    assert "if typing.TYPE_CHECKING:\n    result = 'ran'\n" in "".join(lines)


def test_generated_guard_has_no_binding_a_concurrent_writer_can_change() -> None:
    lines = ["TYPE_CHECKING = True\n", "_type_checking = True\n"]
    UnificationRefactorEngine()._ensure_type_checking_import(lines, "missing_module", "Thing")
    source = "".join(lines)
    (guard,) = [statement for statement in ast.parse(source).body if isinstance(statement, ast.If)]
    assert not any(isinstance(node, ast.Name) for node in ast.walk(guard.test))
    namespace: dict[str, object] = {}
    exec(source, namespace)
    assert namespace["TYPE_CHECKING"] is namespace["_type_checking"] is True
    assert "_Thing" not in namespace


@pytest.mark.parametrize(
    "source, count",
    [
        ("flag = False\nif flag:\n    import absent\n", 0),
        ("if 0 > 1:\n    import absent\n", 1),
        ("if False > True:\n    import absent\n", 0),
        ("if 1 > 0:\n    import absent\n", 0),
        ("if False:\n    import absent\n", 1),
        ("import typing\nif typing.TYPE_CHECKING:\n    import absent\n", 0),
        ("flag = False\nchange_flag()\nif flag:\n    import absent\n", 0),
        ("flag: change_flag() = False\nif flag:\n    import absent\n", 0),
        ("flag = False\nclass Child:\n    if flag:\n        import absent\n", 0),
        ("flag = False\ndef late():\n    if flag:\n        import absent\n", 0),
    ],
)
def test_runtime_false_proof_uses_only_immutable_literal_operands(source: str, count: int) -> None:
    assert len(module_false_guards(ast.parse(source))) == count


def test_import_model_keeps_mutable_spelled_flags_as_possible_optional_imports(
    tmp_path: Path,
) -> None:
    source = (
        "class Flags:\n    TYPE_CHECKING = True\nflags = Flags()\n"
        "if flags.TYPE_CHECKING:\n    import module_that_does_not_exist\n"
    )
    (site,) = _scan_tree(ast.parse(source), tmp_path / "m.py").sites or ()
    assert site.runtime and site.guarded and not site.loads and not site.attests
    ran = _run(tmp_path, "-c", source)
    assert ran.returncode != 0 and "ModuleNotFoundError" in ran.stderr


def test_intervening_import_can_change_a_locally_false_flag(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("flag = False\nimport b\nif flag:\n    print('ran')\n")
    (tmp_path / "b.py").write_text("import a\na.flag = True\n")
    source = (tmp_path / "a.py").read_text()
    assert not module_false_guards(ast.parse(source))
    ran = _run(tmp_path, "-c", "import a")
    assert (ran.returncode, ran.stdout) == (0, "ran\n"), ran.stderr
