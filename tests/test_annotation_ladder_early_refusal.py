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

"""Only parameter loosening with a proved unchanged Any result is redundant."""

from __future__ import annotations

import ast
import copy
import dataclasses
import importlib.util
from pathlib import Path
from typing import Iterator, Mapping, Optional, Sequence, Tuple

import pytest

from towel.type_inference import (
    CheckSuccess,
    MypyInferrer,
    TypeDiagnostic,
    TypeOracle,
    RevealKey,
    RevealRequest,
    Subtyping,
    CombinedOracle,
    PyrightOracle,
    _RelocatedOracle,
)
from towel.unification.annotation_ladder import Hearing, Rejection, any_result_still_refused
from towel.unification.annotation_wiring import (
    _LadderPolicy,
    _mypy_without_plugins,
    _mypy_config_identity,
    mypy_ladder_flags,
    _builtin_oracle,
    verifies_with_mypy,
)
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

MODULE = """from typing import Any, Callable

def helper(callback: Callable[[int], int], value: int) -> Any:
    return callback(value)

def caller() -> int:
    return helper(lambda value: value, 1)
"""


def _helper(source: str = MODULE) -> ast.FunctionDef:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "helper"
    )


def _refusal(
    source: str = MODULE,
    message: str = 'Returning Any from function declared to return "int"',
    line: Optional[int] = 7,
) -> Rejection:
    return Rejection(
        (TypeDiagnostic("/project/m.py", message, line),), "/project/m.py", "helper", source
    )


def _loosened(helper: ast.FunctionDef) -> ast.FunctionDef:
    changed = copy.deepcopy(helper)
    changed.args.args[0].annotation = ast.Name(id="Any", ctx=ast.Load())
    return changed


def _still_refused(
    helper: ast.FunctionDef, rejection: Rejection, checked_helper: Optional[ast.FunctionDef] = None
) -> bool:
    return any_result_still_refused(
        helper, rejection, checked_helper or _helper(rejection.helper_module)
    )


def test_parameter_loosening_cannot_repair_a_direct_any_return() -> None:
    assert _still_refused(_loosened(_helper()), _refusal())


@pytest.mark.parametrize("result", ["int", "T", "Any | int", "object"])
def test_precise_and_correlated_generic_results_keep_their_chance(result: str) -> None:
    helper = _loosened(_helper())
    helper.returns = ast.parse(result, mode="eval").body
    assert not _still_refused(helper, _refusal())


@pytest.mark.parametrize("line", [None, 3, 6, 8])
def test_missing_or_different_error_location_keeps_another_check(line: Optional[int]) -> None:
    assert not _still_refused(_loosened(_helper()), _refusal(line=line))


@pytest.mark.parametrize(
    "message",
    [
        'Argument 1 to "helper" has incompatible type "str"; expected "int"',
        'Incompatible return value type (got "str", expected "int")',
        "checker unavailable",
    ],
)
def test_other_diagnostics_do_not_prove_the_remaining_variant_fails(message: str) -> None:
    assert not _still_refused(_loosened(_helper()), _refusal(message=message))


@pytest.mark.parametrize(
    "statement",
    ["return int(helper(lambda value: value, 1))", "return obj.helper(1)", "return helper(1) + 1"],
)
def test_a_derived_or_dispatched_result_is_left_to_the_checker(statement: str) -> None:
    source = MODULE.replace("return helper(lambda value: value, 1)", statement)
    assert not _still_refused(_loosened(_helper(source)), _refusal(source))


@pytest.mark.parametrize(
    "shadow",
    [
        "Any = int",
        "from other import Any",
        "from other import *",
        "def unrelated(Any: int) -> None: pass",
        "try: pass\nexcept Exception as Any: pass",
        "match 1:\n    case Any: pass",
    ],
)
def test_shadowed_or_ambiguous_any_is_not_a_proof(shadow: str) -> None:
    source = MODULE + "\n" + shadow + "\n"
    assert not _still_refused(_loosened(_helper(source)), _refusal(source))


def test_changed_body_or_parameter_binding_is_not_only_annotation_loosening() -> None:
    helper = _loosened(_helper())
    helper.body = [ast.Return(value=ast.Constant(value=1))]
    assert not _still_refused(helper, _refusal())
    helper = _loosened(_helper())
    helper.args.args[0].arg = "other"
    assert not _still_refused(helper, _refusal())


@pytest.mark.parametrize(
    "configuration,known",
    [
        ("[tool.mypy]\nstrict = true\n", True),
        ("[tool.mypy]\nstrict = true\nplugins = []\n", True),
        ('[tool.mypy]\nstrict = true\nplugins = ["plugin"]\n', False),
        ("[tool.mypy]\nstrict = [\n", False),
        ('[project]\nname = "p"\n', False),
    ],
)
def test_plugin_absence_requires_a_readable_explicit_configuration(
    tmp_path: Path, configuration: str, known: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(configuration)
    path = tmp_path / "m.py"
    path.write_text(MODULE)
    assert _mypy_without_plugins(path) is known


@pytest.mark.parametrize("plugins,known", [("", True), ("plugin", False), ("%(missing)s", False)])
def test_ini_plugin_options_are_not_assumed_absent(
    tmp_path: Path, plugins: str, known: bool
) -> None:
    (tmp_path / "mypy.ini").write_text("[mypy]\nplugins = " + plugins + "\n")
    path = tmp_path / "m.py"
    path.write_text(MODULE)
    assert _mypy_without_plugins(path) is known


def test_absent_configuration_preserves_fallbacks(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(MODULE)
    assert not _mypy_without_plugins(path)


def _configured_policy(
    engine: UnificationRefactorEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    plugins_absent: bool = True,
    returning_any_refused: bool = True,
) -> _LadderPolicy:
    config = tmp_path / "pyproject.toml"
    config.write_text(
        "[tool.mypy]\ndisallow_untyped_defs = false\ncheck_untyped_defs = false\nwarn_return_any = "
        + str(returning_any_refused).lower()
        + "\n"
    )
    path = tmp_path / "m.py"
    path.write_text(MODULE)
    monkeypatch.setattr(engine, "_origin_of", lambda file: str(path))
    flags = mypy_ladder_flags(path)
    return _LadderPolicy(
        mypy=True,
        builtin_mypy=True,
        plugins_absent=plugins_absent,
        configuration=_mypy_config_identity(path),
        annotations_required=flags["disallow_untyped_defs"],
        returning_any_refused=flags["warn_return_any"],
        untyped_bodies_checked=flags["check_untyped_defs"],
    )


@pytest.mark.parametrize("plugins_absent", [True, False])
def test_ladder_skips_only_redundant_fallback_after_real_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plugins_absent: bool
) -> None:
    engine = UnificationRefactorEngine()
    ordinary = RefactoringProposal("/project/m.py", _helper(), [], "ordinary", 2)
    targeted = dataclasses.replace(ordinary, extracted_function=_loosened(_helper()))
    generic_helper = _helper()
    generic_helper.returns = ast.Name(id="T", ctx=ast.Load())
    generic = dataclasses.replace(ordinary, extracted_function=generic_helper)
    monkeypatch.setattr(engine, "_judged_by_no_checker", lambda proposal: False)
    monkeypatch.setattr(engine, "_untypeable_as_proposed", lambda proposal: None)
    policy = _configured_policy(engine, monkeypatch, tmp_path, plugins_absent=plugins_absent)
    monkeypatch.setattr(engine, "_ladder_policy", lambda path: policy)
    monkeypatch.setattr(engine, "_helper_uses_any", lambda proposal: True)
    monkeypatch.setattr(engine, "_generic_helper_variants", lambda proposal: iter([generic]))
    monkeypatch.setattr(engine, "_targeted_variant", lambda proposal, refusal: targeted)
    hearing = Hearing(lambda helper, rejection: None)
    ladder = engine._annotation_ladder(ordinary, True, hearing)
    assert next(ladder) is generic  # A correlated result is never filtered.
    assert next(ladder) is ordinary
    hearing.refused(ordinary, _refusal())
    remaining = list(ladder)
    assert remaining == ([] if plugins_absent else [targeted])
    assert hearing.settled_by() is None  # This does not prove the whole proposal impossible.


def test_an_unknown_or_missing_checker_answer_keeps_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = UnificationRefactorEngine()
    ordinary = RefactoringProposal("/project/m.py", _helper(), [], "ordinary", 2)
    monkeypatch.setattr(engine, "_judged_by_no_checker", lambda proposal: False)
    monkeypatch.setattr(engine, "_untypeable_as_proposed", lambda proposal: None)
    policy = _configured_policy(engine, monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "_ladder_policy", lambda path: policy)
    monkeypatch.setattr(engine, "_helper_uses_any", lambda proposal: True)
    monkeypatch.setattr(engine, "_generic_helper_variants", lambda proposal: iter(()))

    def targeted(proposal: RefactoringProposal, refusal: Rejection) -> RefactoringProposal:
        return dataclasses.replace(
            proposal, extracted_function=_loosened(proposal.extracted_function)
        )

    monkeypatch.setattr(engine, "_targeted_variant", targeted)
    for rejection in (None, _refusal(message="checker unavailable")):
        hearing = Hearing(lambda helper, refusal: None)
        ladder: Iterator[RefactoringProposal] = engine._annotation_ladder(ordinary, True, hearing)
        first = next(ladder)
        if rejection is not None:
            hearing.refused(first, rejection)
        assert len(list(ladder)) == (0 if rejection is None else 1)


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_actual_mypy_keeps_correlated_success_and_repeats_any_return_refusal(
    tmp_path: Path,
) -> None:
    """One generic signature works; erasing arguments cannot repair its erased result."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    path = tmp_path / "m.py"
    ordinary = MODULE.replace("Callable[[int], int]", "Callable[[str], str]")
    targeted = ordinary.replace(
        "callback: Callable[[str], str], value: int", "callback: Any, value: Any"
    )
    generic = MODULE.replace(
        "from typing import Any, Callable",
        'from typing import Any, Callable, TypeVar\nT = TypeVar("T")',
    ).replace("Callable[[int], int], value: int) -> Any", "Callable[[T], T], value: T) -> T")
    path.write_text(generic)
    oracle = MypyInferrer()
    try:
        baseline = oracle.check_project({str(path): generic})
        assert isinstance(baseline, CheckSuccess) and not baseline.errors
        first = oracle.check_project({str(path): ordinary})
        second = oracle.check_project({str(path): targeted})
        assert isinstance(first, CheckSuccess)
        assert isinstance(second, CheckSuccess)
        first_any = [error for error in first.errors if error.message.startswith("Returning Any")]
        second_any = [error for error in second.errors if error.message.startswith("Returning Any")]
        assert len(first_any) == len(second_any) == 1
        assert first_any[0].message == second_any[0].message
        assert len(first.errors) > len(second.errors) == 1
        refusal = Rejection(tuple(first.errors), str(path), "helper", ordinary)
        assert _still_refused(_helper(targeted), refusal)
        assert not _still_refused(_helper(generic), refusal)
        restored = oracle.check_project({str(path): generic})
        assert isinstance(restored, CheckSuccess) and not restored.errors
    finally:
        oracle.close()


@pytest.mark.parametrize("annotation", ["Any", '"Any"', "typing.Any"])
def test_plain_quoted_and_qualified_typing_any_are_proved(annotation: str) -> None:
    source = MODULE.replace("-> Any:", "-> " + annotation + ":")
    line = 7
    if annotation == "typing.Any":
        source = "import typing\n" + source
        line += 1
    assert _still_refused(_loosened(_helper(source)), _refusal(source, line=line))


def test_decorated_helper_and_missing_result_annotation_keep_their_chance() -> None:
    source = MODULE.replace("def helper", "@decorate\ndef helper")
    assert not _still_refused(_loosened(_helper(source)), _refusal(source, line=8))
    helper = _loosened(_helper())
    helper.returns = None
    assert not _still_refused(helper, _refusal())


@pytest.mark.parametrize("plugins_absent", [True, False])
def test_every_any_fallback_also_requires_the_exact_witness_and_plugin_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plugins_absent: bool
) -> None:
    engine = UnificationRefactorEngine()
    ordinary = RefactoringProposal("/project/m.py", _helper(), [], "ordinary", 2)
    monkeypatch.setattr(engine, "_judged_by_no_checker", lambda proposal: False)
    monkeypatch.setattr(engine, "_untypeable_as_proposed", lambda proposal: None)
    policy = _configured_policy(
        engine, monkeypatch, tmp_path, plugins_absent=plugins_absent, returning_any_refused=False
    )
    monkeypatch.setattr(engine, "_ladder_policy", lambda path: policy)
    monkeypatch.setattr(engine, "_helper_uses_any", lambda proposal: True)
    monkeypatch.setattr(engine, "_generic_helper_variants", lambda proposal: iter(()))
    monkeypatch.setattr(engine, "_targeted_variant", lambda proposal, refusal: None)
    hearing = Hearing(lambda helper, rejection: None)
    ladder = engine._annotation_ladder(ordinary, True, hearing)
    assert next(ladder) is ordinary
    hearing.refused(ordinary, _refusal())
    remaining = list(ladder)
    assert len(remaining) == (0 if plugins_absent else 1)
    if remaining:
        result = remaining[0].extracted_function.returns
        assert result is not None and ast.unparse(result) == "Any"
    assert hearing.settled_by() is None


@pytest.mark.parametrize("field", ["name", "type_comment"])
def test_function_identity_and_type_comment_cannot_change(field: str) -> None:
    helper = _loosened(_helper())
    setattr(helper, field, "different")
    assert not _still_refused(helper, _refusal())


@pytest.mark.skipif(not hasattr(ast, "TypeVar"), reason="PEP 695 requires Python 3.12")
def test_new_any_named_type_parameter_cannot_shadow_imported_any() -> None:
    helper = _helper(MODULE.replace("def helper(", "def helper[Any]("))
    assert not _still_refused(helper, _refusal())


def test_allocated_rendered_name_is_distinct_from_candidate_identity() -> None:
    checked = _helper()
    checked.name = "__extracted_func"
    candidate = _loosened(checked)
    source = MODULE.replace("helper", "_extracted_func_0")
    rejection = Rejection(
        (
            TypeDiagnostic(
                "/project/m.py", 'Returning Any from function declared to return "int"', 7
            ),
        ),
        "/project/m.py",
        "_extracted_func_0",
        source,
    )
    assert any_result_still_refused(candidate, rejection, checked)
    candidate.name = "a_different_helper"
    assert not any_result_still_refused(candidate, rejection, checked)


class _FabricatedOracle(TypeOracle):
    """A custom checker deliberately returning mypy-shaped diagnostics."""

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        raise AssertionError("No inference should be launched by the capability guard")

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        raise AssertionError("No subtype check should be launched by the capability guard")

    def check(self, file_path: str, source: str) -> CheckSuccess:
        return self.check_project({file_path: source})

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckSuccess:
        return CheckSuccess(
            (
                TypeDiagnostic(
                    next(iter(sources), "m.py"),
                    'Returning Any from function declared to return "int"',
                    7,
                ),
            )
        )

    def close(self) -> None:
        return None


def test_custom_oracles_do_not_enable_mypy_specific_ladder_proofs() -> None:
    from towel.unification.annotation_wiring import verifies_with_mypy

    assert not verifies_with_mypy(_FabricatedOracle())
    assert not verifies_with_mypy(None)


def test_custom_mypy_subclasses_and_compositions_do_not_certify_call_semantics(
    tmp_path: Path,
) -> None:
    class CustomMypy(MypyInferrer):
        def check_project(
            self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
        ) -> CheckSuccess:
            return _FabricatedOracle().check_project(sources, excluded_paths=excluded_paths)

    class CustomCombined(CombinedOracle):
        pass

    class CustomRelocated(_RelocatedOracle):
        pass

    mypy = MypyInferrer()
    pyright = PyrightOracle()
    subclass = CustomMypy()
    custom = _FabricatedOracle()
    try:
        assert (
            subclass.check_project({"m.py": MODULE}).errors[0].message.startswith("Returning Any")
        )
        assert verifies_with_mypy(subclass)  # Existing broad dispatch remains unchanged.
        assert not _builtin_oracle(subclass)
        assert _builtin_oracle(mypy)
        assert _builtin_oracle(CombinedOracle(mypy, [pyright]))
        assert _builtin_oracle(_RelocatedOracle(mypy, tmp_path / "original", tmp_path / "output"))
        assert not _builtin_oracle(CombinedOracle(mypy, [custom]))
        assert not _builtin_oracle(CustomCombined(mypy, []))
        assert not _builtin_oracle(
            CustomRelocated(mypy, tmp_path / "original", tmp_path / "output")
        )
        assert not _builtin_oracle(None)
    finally:
        mypy.close()
        pyright.close()
        subclass.close()


@pytest.mark.parametrize("callback_field", ["snippet_formatter", "file_finisher"])
def test_custom_rendering_callbacks_preserve_the_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, callback_field: str
) -> None:
    engine = UnificationRefactorEngine()
    monkeypatch.setattr(engine, callback_field, lambda *arguments: "different return type")
    ordinary = RefactoringProposal("/project/m.py", _helper(), [], "ordinary", 2)
    targeted = dataclasses.replace(ordinary, extracted_function=_loosened(_helper()))
    monkeypatch.setattr(engine, "_judged_by_no_checker", lambda proposal: False)
    monkeypatch.setattr(engine, "_untypeable_as_proposed", lambda proposal: None)
    policy = _configured_policy(engine, monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "_ladder_policy", lambda path: policy)
    monkeypatch.setattr(engine, "_helper_uses_any", lambda proposal: True)
    monkeypatch.setattr(engine, "_generic_helper_variants", lambda proposal: iter(()))
    monkeypatch.setattr(engine, "_targeted_variant", lambda proposal, refusal: targeted)
    hearing = Hearing(lambda helper, rejection: None)
    ladder = engine._annotation_ladder(ordinary, True, hearing)
    assert next(ladder) is ordinary
    hearing.refused(ordinary, _refusal())
    assert list(ladder) == [targeted]


@pytest.mark.skipif(not hasattr(ast, "TypeVar"), reason="PEP 695 requires Python 3.12")
def test_existing_any_type_parameter_is_not_an_imported_typing_any_result() -> None:
    source = MODULE.replace("def helper(", "def helper[Any](")
    assert not _still_refused(_loosened(_helper(source)), _refusal(source))


@pytest.mark.parametrize("change", ["plugin", "warn-return-any", "ci-strict"])
def test_configuration_changes_between_rungs_retain_the_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str
) -> None:
    engine = UnificationRefactorEngine()
    workflow = tmp_path / ".github/workflows/types.yml"
    if change == "ci-strict":
        workflow.parent.mkdir(parents=True)
        workflow.write_text("jobs:\n  types:\n    steps:\n      - run: mypy --strict m.py\n")
    policy = _configured_policy(
        engine, monkeypatch, tmp_path, returning_any_refused=change != "ci-strict"
    )
    ordinary = RefactoringProposal("/project/m.py", _helper(), [], "ordinary", 2)
    targeted = dataclasses.replace(ordinary, extracted_function=_loosened(_helper()))
    monkeypatch.setattr(engine, "_judged_by_no_checker", lambda proposal: False)
    monkeypatch.setattr(engine, "_untypeable_as_proposed", lambda proposal: None)
    monkeypatch.setattr(engine, "_ladder_policy", lambda path: policy)
    monkeypatch.setattr(engine, "_helper_uses_any", lambda proposal: True)
    monkeypatch.setattr(engine, "_generic_helper_variants", lambda proposal: iter(()))
    monkeypatch.setattr(engine, "_targeted_variant", lambda proposal, refusal: targeted)
    hearing = Hearing(lambda helper, rejection: None)
    ladder = engine._annotation_ladder(ordinary, True, hearing)
    assert next(ladder) is ordinary
    assert engine._fixed_any_result(policy, ordinary.file_path)
    hearing.refused(ordinary, _refusal())
    config = tmp_path / "pyproject.toml"
    if change == "plugin":
        config.write_text(config.read_text() + 'plugins = ["plugin"]\n')
    elif change == "warn-return-any":
        config.write_text(
            config.read_text().replace("warn_return_any = true", "warn_return_any = false")
        )
    else:
        workflow.write_text(workflow.read_text().replace("--strict ", ""))
    assert not engine._fixed_any_result(policy, ordinary.file_path)
    assert list(ladder) == [targeted]
