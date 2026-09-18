"""Behavioral coverage follows replacement locations, including clustered occurrences."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.automatic_equivalence_tester import AutomaticEquivalenceTester, check_affected_functions
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
from tests.equivalence_targets import affected_functions
from towel.unification.models import RefactoringProposal, Replacement
from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = "    a = x + 1\n    b = a * 2\n    c = b + 3\n    return c\n"
THREE_FUNCTIONS = "".join(f"def {name}(x):\n" + BODY for name in ("first", "second", "third"))


class CorruptThirdOccurrence(UnificationRefactorEngine):
    """Inject a known defect into a real engine proposal to test the measurement."""

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        tree = ast.parse(super().apply_refactoring(file_path, proposal))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "third":
                node.body = [ast.Return(value=ast.Constant(value="incorrect"))]
        return ast.unparse(ast.fix_missing_locations(tree))


def test_clustered_third_occurrence_is_verified_despite_two_name_description(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cluster.py"
    source.write_text(THREE_FUNCTIONS)
    # Keep the three-site helper: reusing ``first`` would leave two sites.
    engine = UnificationRefactorEngine(reuse_existing_functions=False)
    proposal = engine.analyze_file(str(source))[0]
    assert len(proposal.replacements) == 3
    assert "third" not in proposal.description
    assert affected_functions(proposal, {source: THREE_FUNCTIONS}) == {
        source.resolve(): ("first", "second", "third")
    }
    passed, failed, errors = AutomaticEquivalenceTester(CorruptThirdOccurrence()).test_file(
        str(source)
    )
    assert passed == 0 and failed == 1
    assert any("third:" in error and "incorrect" in error for error in errors)
    assert AutomaticEquivalenceTester(engine).test_file(str(source)) == (1, 0, [])


def proposal_for(source: Path, code: str, start: int, end: int) -> RefactoringProposal:
    helper = ast.parse("def helper():\n    pass\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    return RefactoringProposal(
        file_path=str(source),
        extracted_function=helper,
        replacements=[Replacement((start, end), ast.Pass(), str(source))],
        description="Deliberately unrelated presentation text",
        parameters_count=0,
    )


def test_nested_replacement_selects_its_actual_top_level_factory(tmp_path: Path) -> None:
    code = "def factory(x):\n    def inner():\n        return x + 1\n    return inner\n"
    source = tmp_path / "nested.py"
    selection = affected_functions(proposal_for(source, code, 3, 3), {source: code})
    assert selection[source.resolve()] == ("factory",)
    assert check_affected_functions(code, code, selection[source.resolve()])[0]
    assert not check_affected_functions(code, code.replace("x + 1", "x + 2"), ("factory",))[0]


METHOD_SOURCE = """class Processor:
    def __init__(self, email):
        self.email = email
        self.calls = 0

    def process(self):
        self.calls += 1
        return self.email
"""


def test_class_method_is_invoked_with_constructor_inputs_and_instance_state(tmp_path: Path) -> None:
    source = tmp_path / "methods.py"
    selected = affected_functions(
        proposal_for(source, METHOD_SOURCE, 7, 8), {source: METHOD_SOURCE}
    )
    targets = selected[source.resolve()]
    assert targets == ("Processor.process",)
    assert check_affected_functions(METHOD_SOURCE, METHOD_SOURCE, targets) == (True, [])
    changed_return = METHOD_SOURCE.replace("return self.email", "return 'corrupted'")
    passed, errors = check_affected_functions(METHOD_SOURCE, changed_return, targets)
    assert not passed and any("corrupted" in error for error in errors)
    changed_state = METHOD_SOURCE.replace("self.calls += 1", "self.calls += 2")
    assert not check_affected_functions(METHOD_SOURCE, changed_state, targets)[0]


@pytest.mark.parametrize("decorator, binder", [("staticmethod", ""), ("classmethod", "cls, ")])
def test_static_and_class_methods_preserve_dispatch(decorator: str, binder: str) -> None:
    code = (
        f"class Processor:\n    @{decorator}\n    def process({binder}x):\n"
        "        return x + 1\n"
    )
    assert check_affected_functions(code, code, ("Processor.process",)) == (True, [])
    assert not check_affected_functions(
        code, code.replace("x + 1", "x + 2"), ("Processor.process",)
    )[0]


def test_crossfile_qualified_method_adapter_uses_imported_class(tmp_path: Path) -> None:
    original, changed = tmp_path / "original", tmp_path / "changed"
    for root, code in (
        (original, METHOD_SOURCE),
        (changed, METHOD_SOURCE.replace("self.calls += 1", "self.calls += 2")),
    ):
        root.mkdir()
        (root / "methods.py").write_text(code)
    passed, errors = CrossFileEquivalenceTester(
        UnificationRefactorEngine()
    )._compare_project_behavior(
        original, changed, {str(original / "methods.py"): ["Processor.process"]}
    )
    assert not passed and any("Processor.process" in error for error in errors)


@pytest.mark.parametrize(
    "code, target, reason",
    [
        (
            "class Base: pass\nclass C(Base):\n    def f(self): return 1\n",
            "C.f",
            "C.f: inherited, decorated, or metaclass construction is unsupported",
        ),
        (
            "class C:\n    def __init__(self, *, x): pass\n    def f(self): return 1\n",
            "C.f",
            "C.f: exotic constructor/method signatures are unsupported",
        ),
        ("def f(): return 1\ndef f(): return 2\n", "f", "No unique top-level function f"),
    ],
)
def test_unsupported_or_ambiguous_callable_is_unverified(
    code: str, target: str, reason: str
) -> None:
    passed, errors = check_affected_functions(code, code, (target,))
    assert not passed
    assert errors == [f"{target}: equivalence was not tested: {reason}"]


def test_same_constructor_failures_do_not_claim_method_equivalence() -> None:
    source = (
        "class Processor:\n    def __init__(self):\n        raise ValueError('cannot construct')\n"
        "    def process(self):\n        return 1\n"
    )
    passed, errors = check_affected_functions(source, source, ("Processor.process",))
    assert not passed
    assert any("targets invoked: False/False" in error for error in errors)
