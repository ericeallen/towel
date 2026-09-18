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
