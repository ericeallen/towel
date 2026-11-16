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

"""Usability finding 6: installing types must not hide bare extractions."""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer, TypeDiagnostic
from towel.unification.exceptions import UnverifiableChangeError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_type_run_policy import _Oracle, _source


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("annotate_helpers", [False, True])
def test_import_gate_depends_on_callers_even_when_annotation_is_disabled(
    tmp_path: Path, strict: bool, typed: bool, annotate_helpers: bool
) -> None:
    """Finding 6: only relaxed proposals whose callers and helper are bare bypass the gate."""
    path = tmp_path / "program.py"
    original = "import absent_dependency\n" + _source(typed=typed)
    path.write_text(original)
    missing = TypeDiagnostic(
        str(path),
        'Cannot find implementation or library stub for module named "absent_dependency"',
        1,
    )
    oracle = _Oracle(lambda _: CheckSuccess((missing,)))
    engine = UnificationRefactorEngine(
        type_oracle=oracle, strict=strict, annotate_helpers=annotate_helpers
    )
    proposal = engine.analyze_file(str(path))[0]
    if strict or typed:
        with pytest.raises(UnverifiableChangeError):
            engine.apply_refactoring(str(path), proposal)
    else:
        result = engine.apply_refactoring(str(path), proposal)
        helper = next(
            node
            for node in ast.parse(result).body
            if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
        )
        assert helper.returns is None
        assert all(arg.annotation is None for arg in helper.args.args)
        assert len(oracle.checks) == 2, "Actual new type errors are still checked"
    assert path.read_text() == original


def test_relaxed_import_notice_is_once_per_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Finding 6: a direct caller gets one explanation, reset for each explicit run."""
    path = tmp_path / "program.py"
    path.write_text("import absent_dependency\n" + _source(typed=False))
    missing = TypeDiagnostic(
        str(path),
        'Cannot find implementation or library stub for module named "absent_dependency"',
        1,
    )
    # Relaxed mode's notice, whatever the suite's default mode.
    engine = UnificationRefactorEngine(
        type_oracle=_Oracle(lambda _: CheckSuccess((missing,))), strict=False
    )
    proposal = engine.analyze_file(str(path))[0]
    for _ in range(2):
        engine.apply_refactoring(str(path), proposal)
    notice = "Unresolved imports do not block this unannotated extraction in relaxed mode"
    assert caplog.text.count(notice) == 1
    assert "No change to these" not in caplog.text
    engine.begin_refactoring_run([str(path)])
    engine.apply_refactoring(str(path), proposal)
    assert caplog.text.count(notice) == 2


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_scenario_16_with_real_mypy_and_unavailable_pytest(
    tmp_path: Path, strict: bool, typed: bool
) -> None:
    """Finding 6, scenario 16: mypy cannot resolve pytest, yet relaxed bare code extracts."""
    pytest.importorskip("mypy")
    fixtures = Path(__file__).parent / "fixtures"
    (tmp_path / "cart.py").write_text((fixtures / "typed_cart.py.txt").read_text())
    source = (fixtures / "typed_test_cart.py.txt").read_text()
    if typed:
        source = source.replace("():", "() -> None:")
    path = tmp_path / "test_cart.py"
    path.write_text(source)
    # Keep the actual checker while excluding its environment's pytest installation.
    (tmp_path / "mypy.ini").write_text("[mypy]\nno_site_packages = True\n")
    oracle = MypyInferrer()
    try:
        baseline = oracle.check(str(path), source)
        assert isinstance(baseline, CheckSuccess)
        assert any('module named "pytest"' in error.message for error in baseline.errors)
        engine = UnificationRefactorEngine(type_oracle=oracle, strict=strict)
        result, count, _ = engine.refactor_to_fixed_point(str(path), progress="none")
        if strict or typed:
            assert count == 0
            assert result == source
        else:
            assert count > 0
            assert "extracted_func" in result
    finally:
        oracle.close()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("annotate_helpers", [False, True])
def test_signature_comments_keep_the_import_gate(
    tmp_path: Path, strict: bool, annotate_helpers: bool
) -> None:
    """Typed round 2 finding 2: signature comments are checked bodies, never bare callers."""
    path = tmp_path / "program.py"
    # The python lane's fixture takes no argument where strict lifetimes are uncertified.
    source = "import absent_dependency\n" + re.sub(
        r"(def (?:first|second)\((value)?\):)",
        lambda found: found[1] + f"\n    # type: ({'int' if found[2] else ''}) -> int",
        _source(typed=False),
    )
    assert "# type:" in source
    path.write_text(source)
    missing = TypeDiagnostic(
        str(path),
        'Cannot find implementation or library stub for module named "absent_dependency"',
        1,
    )
    engine = UnificationRefactorEngine(
        type_oracle=_Oracle(lambda _: CheckSuccess((missing,))),
        strict=strict,
        annotate_helpers=annotate_helpers,
    )
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(UnverifiableChangeError):
        engine.apply_refactoring(str(path), proposal)
    assert path.read_text() == source


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("command", ["preview", "dry"])
def test_unresolved_summary_names_user_file_and_counts_modules(
    tmp_path: Path, directory: bool, command: str
) -> None:
    """Typed round 2 finding 4: staged runs report user paths and unique missing imports."""
    from tests.test_cli_integration import invoke

    pytest.importorskip("mypy")
    (tmp_path / "mypy.ini").write_text("[mypy]\nno_site_packages = True\n")
    target = tmp_path / "selected"
    target.mkdir()
    path = target / "program.py"
    source = "import absent_dependency\n" + _source(typed=True)
    path.write_text(source)
    selected = target if directory else path
    arguments = [command, str(selected)]
    if command == "dry":
        arguments += [str(selected), "--no-interactive"]
    result = invoke([*arguments, "--no-format", "--progress", "none"])
    assert result.status == 0, result.stderr
    summary = next(line for line in result.stdout.splitlines() if "First problem:" in line)
    assert "First problem: program.py" in summary
    assert "1 unresolved module(s)" in summary
    assert "absent_dependency" in summary
    assert "towel-stage-" not in summary
    assert path.read_text() == source
