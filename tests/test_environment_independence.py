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

"""The fifth audit's environment findings: what a run must not depend on.

The result of a refactoring must not depend on how the paths were spelled,
where the process stands, which characters end a line, or what another run
left behind. Each test here pins one of those, by execution.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import textwrap

import pytest

from towel.changes import ChangePlan, RecoveryRequired, apply_changes
from towel.source_text import source_lines
from towel.type_inference import _same_file, is_probe_file
from towel.unification.import_graph import ImportGraphCache, import_runs_new_code
from towel.unification.pipeline import AnalysisSession
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import available_argument_names
from towel.unification.visitors import FreeNameCollector

TWO_ROUNDS = textwrap.dedent("""
    def alpha(items):
        total = 0
        for item in items:
            total += item
        scaled = total * 2
        return scaled

    def beta(items):
        total = 0
        for item in items:
            total += item
        scaled = total * 2
        return scaled + 1
    """)


def test_a_relative_output_directory_reaches_the_same_fixed_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("absolute", "relative"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "m.py").write_text(TWO_ROUNDS)
        (tmp_path / name / "n.py").write_text(
            TWO_ROUNDS.replace("alpha", "gamma").replace("beta", "delta")
        )
    absolute = UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
        str(tmp_path / "absolute"), str(tmp_path / "absolute"), progress="none"
    )
    monkeypatch.chdir(tmp_path)
    relative = UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
        "relative", "relative", progress="none"
    )
    applied_absolute = sum(count for count, _ in absolute[0].values())
    applied_relative = sum(count for count, _ in relative[0].values())
    assert applied_relative == applied_absolute > 0
    assert (tmp_path / "relative" / "m.py").read_text() == (
        tmp_path / "absolute" / "m.py"
    ).read_text()


def test_a_checker_path_relative_to_the_working_directory_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "out" / "m.py"
    assert _same_file("out/m.py", str(target))
    assert _same_file(str(target), "out/m.py")
    assert not _same_file("elsewhere/m.py", str(target))


def test_source_lines_count_lines_the_way_the_tokenizer_does() -> None:
    text = "a = 1\x0c\nb = '\u2028'\nc = 2  # \x85\n"
    lines = source_lines(text)
    assert lines == ["a = 1\x0c\n", "b = '\u2028'\n", "c = 2  # \x85\n"]
    assert len(lines) == len(ast.parse(text).body)
    assert source_lines("x") == ["x"]
    assert source_lines("") == []


def test_a_pending_journal_blocks_only_the_files_it_names(tmp_path: Path) -> None:
    covered = tmp_path / "covered.py"
    other = tmp_path / "other.py"
    covered.write_text("x = 1\n")
    other.write_text("y = 1\n")
    journal = tmp_path / ".towel-transaction-leftover"
    journal.mkdir(mode=0o700)
    (journal / "manifest.json").write_text(
        json.dumps([{"path": "covered.py", "mode": 0o644, "before": "a", "after": "b"}])
    )
    apply_changes(ChangePlan.from_sources({str(other): b"y = 1\n"}, {str(other): "y = 2\n"}))
    assert other.read_text() == "y = 2\n"
    with pytest.raises(RecoveryRequired, match="leftover"):
        apply_changes(
            ChangePlan.from_sources({str(covered): b"x = 1\n"}, {str(covered): "x = 2\n"})
        )
    assert covered.read_text() == "x = 1\n"


def test_a_journal_without_a_manifest_blocks_everything_beneath_it(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    target = tmp_path / "sub" / "m.py"
    target.write_text("x = 1\n")
    (tmp_path / ".towel-transaction-midway").mkdir(mode=0o700)
    with pytest.raises(RecoveryRequired):
        apply_changes(ChangePlan.from_sources({str(target): b"x = 1\n"}, {str(target): "x = 2\n"}))


def test_journals_of_concurrent_runs_never_share_a_name(tmp_path: Path) -> None:
    names = set()
    for index in range(3):
        target = tmp_path / f"m{index}.py"
        target.write_text("x = 1\n")
        seen_before = {p.name for p in tmp_path.glob(".towel-transaction-*")}
        apply_changes(ChangePlan.from_sources({str(target): b"x = 1\n"}, {str(target): "x = 2\n"}))
        assert seen_before == set(), "a committed transaction leaves no journal"
        names.add(index)
    assert len(names) == 3


def test_a_pyright_probe_is_never_discovered_as_source(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("x = 1\n")
    (tmp_path / "_towel_probe_real_abc123.py").write_text("x = 1\n")
    assert is_probe_file(tmp_path / "_towel_probe_real_abc123.py")
    found = UnificationRefactorEngine()._find_python_files(str(tmp_path), True)
    assert [Path(path).name for path in found] == ["real.py"]


def test_a_virtual_environment_is_recognized_by_its_marker(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("x = 1\n")
    env = tmp_path / "myenv" / "lib" / "site"
    env.mkdir(parents=True)
    (tmp_path / "myenv" / "pyvenv.cfg").write_text("home = /usr\n")
    (env / "vendored.py").write_text("x = 1\n")
    found = UnificationRefactorEngine()._find_python_files(str(tmp_path), True)
    assert [Path(path).name for path in found] == ["real.py"]


def test_a_package_named_like_an_environment_is_project_source(tmp_path: Path) -> None:
    """``env`` and ``venv`` are names a project may give its own package; a marker makes an environment."""
    for relative in (
        "real.py",
        "env/__init__.py",
        "venv/mod.py",
        "myvenv/lib/vendored.py",
        "conda_env/lib/vendored.py",
        "node_modules/tool.py",
        "__pycache__/cached.py",
    ):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("x = 1\n")
    (tmp_path / "myvenv" / "pyvenv.cfg").write_text("home = /usr\n")
    (tmp_path / "conda_env" / "conda-meta").mkdir()
    found = UnificationRefactorEngine()._find_python_files(str(tmp_path), True)
    assert sorted(str(Path(path).relative_to(tmp_path)) for path in found) == [
        "env/__init__.py",
        "real.py",
        "venv/mod.py",
    ]


@pytest.mark.parametrize("in_place", [True, False], ids=["in-place", "to-an-output"])
def test_a_package_named_env_is_refactored_like_any_other(tmp_path: Path, in_place: bool) -> None:
    project = tmp_path / "project"
    (project / "env").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    (project / "env" / "__init__.py").write_text("")
    (project / "env" / "m.py").write_text(TWO_ROUNDS)
    output = project if in_place else tmp_path / "output"
    results, _ = UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
        str(project), str(output), progress="none"
    )
    assert sum(applied for applied, _ in results.values()) > 0
    assert (output / "env" / "m.py").read_text() != TWO_ROUNDS


def test_the_session_grows_its_byte_budget_with_the_project(tmp_path: Path) -> None:
    session = AnalysisSession(max_entries=2, max_source_bytes=10)
    files = []
    for index in range(3):
        path = tmp_path / f"m{index}.py"
        path.write_text("value = 1\n" * 4)
        files.append(str(path))
    session.hold_at_least(len(files), sum(os.path.getsize(f) for f in files))
    for name in files:
        session.analyze_module(name)
    assert all(session.reusable(name) for name in files)


def test_class_attributes_are_not_available_to_a_method_call_site() -> None:
    tree = ast.parse(textwrap.dedent("""
            LIMIT = 3
            class C:
                BOUND = 4
                def m(self, flag):
                    total = 0
                    if flag:
                        total += LIMIT
                    return total
            """))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    klass = tree.body[1]
    assert isinstance(klass, ast.ClassDef)
    method = klass.body[1]
    assert isinstance(method, ast.FunctionDef)
    available = available_argument_names(method, method.body[1:], analyzer)
    assert "LIMIT" in available and "total" in available
    assert "BOUND" not in available


def test_module_names_bound_after_the_definition_are_not_available() -> None:
    tree = ast.parse(textwrap.dedent("""
            try:
                import fast
            except ImportError:
                pass
            EARLY = 1
            def f(flag):
                total = 0
                if flag:
                    total += LATE
                return total
            f(False)
            LATE = 2
            """))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[2]
    assert isinstance(function, ast.FunctionDef)
    available = available_argument_names(function, function.body[1:], analyzer)
    assert "EARLY" in available
    assert "LATE" not in available and "fast" not in available


def test_a_helper_import_that_would_run_new_module_code_is_refused(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "host.py").write_text('print("loading host")\ndef helper():\n    return 1\n')
    (package / "quiet.py").write_text("def helper():\n    return 1\n")
    (package / "borrower.py").write_text("def use():\n    return 2\n")
    (package / "importer.py").write_text("from pkg import host\ndef use():\n    return 2\n")
    cache = ImportGraphCache()
    assert import_runs_new_code(str(package / "host.py"), str(package / "borrower.py"), cache)
    assert not import_runs_new_code(str(package / "quiet.py"), str(package / "borrower.py"), cache)
    assert not import_runs_new_code(str(package / "host.py"), str(package / "importer.py"), cache)


def test_a_forwarding_lambda_reads_only_its_callee() -> None:
    call = ast.parse("h(lambda *args, **kwargs: f(*args, **kwargs), lambda x=d: x + y, z)")
    collector = FreeNameCollector()
    collector.visit(call)
    assert collector.used == {"h", "f", "d", "y", "z"}


def test_a_helper_import_that_would_require_a_new_third_party_module_is_refused(
    tmp_path: Path,
) -> None:
    """``import tornado`` is inert, but a borrower made to import its module now needs tornado.

    gunicorn's sync worker stopped importing where tornado was absent. The
    standard library, a declared dependency, an import the borrower already
    makes, and one guarded by ``try`` require nothing new.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0"\ndependencies = ["declared-dep>=1"]\n'
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    files = {
        "needs_tornado.py": "import tornado\ndef helper():\n    return 1\n",
        "needs_tornado_if.py": "import sys\nif sys.version_info > (3,):\n    import tornado\n",
        "optional.py": "try:\n    import tornado\nexcept ImportError:\n    tornado = None\n",
        "stdlib.py": "import json\nfrom os import path\n",
        "declared.py": "import declared_dep\n",
        "borrower.py": "def use():\n    return 2\n",
        "has_tornado.py": "import tornado.web\ndef use():\n    return 2\n",
    }
    for name, text in files.items():
        (package / name).write_text(text)
    cache = ImportGraphCache()

    def refused(host: str, borrower: str = "borrower.py") -> bool:
        return import_runs_new_code(str(package / host), str(package / borrower), cache)

    assert refused("needs_tornado.py")
    assert refused("needs_tornado_if.py")
    assert not refused("optional.py")
    assert not refused("stdlib.py")
    assert not refused("declared.py")
    assert not refused("needs_tornado.py", "has_tornado.py")


@pytest.mark.parametrize(
    "filename, metadata",
    [
        (
            "pyproject.toml",
            '[tool.poetry]\nname = "pkg"\nversion = "0"\n\n[tool.poetry.dependencies]\n'
            'python = "^3.10"\ndeclared-dep = "^1"\n'
            'extra-dep = {version = "^1", optional = true}\n',
        ),
        (
            "setup.cfg",
            "[metadata]\nname = pkg\n\n[options]\ninstall_requires =\n"
            "    declared-dep>=1  # the one this module needs\n"
            "    other; python_version < '3.8'\n",
        ),
    ],
    ids=["poetry", "setup-cfg"],
)
def test_a_dependency_poetry_or_setup_cfg_declares_is_no_new_requirement(
    tmp_path: Path, filename: str, metadata: str
) -> None:
    """Declared dependencies are read where Poetry and setuptools declare them too.

    An optional Poetry dependency is installed only with an extra, so it is
    still a new requirement; so is anything undeclared.
    """
    (tmp_path / filename).write_text(metadata)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    files = {
        "declared.py": "import declared_dep\n",
        "extra.py": "import extra_dep\n",
        "undeclared.py": "import tornado\n",
        "borrower.py": "def use():\n    return 2\n",
    }
    for name, text in files.items():
        (package / name).write_text(text)
    cache = ImportGraphCache()

    def refused(host: str) -> bool:
        return import_runs_new_code(str(package / host), str(package / "borrower.py"), cache)

    assert not refused("declared.py")
    assert refused("extra.py")
    assert refused("undeclared.py")


def test_a_setup_cfg_that_reads_its_requirements_from_a_file_declares_none(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = pkg\n\n[options]\ninstall_requires = file: requirements.txt\n"
    )
    (tmp_path / "requirements.txt").write_text("declared-dep\n")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "declared.py").write_text("import declared_dep\n")
    (package / "borrower.py").write_text("def use():\n    return 2\n")
    assert import_runs_new_code(
        str(package / "declared.py"), str(package / "borrower.py"), ImportGraphCache()
    )
