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

"""Whole-body handoff preserves cleanup without retaining arguments in the caller."""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path
from types import CodeType
from typing import Callable, List, cast

import pytest

from towel.canonical_ast import canonical_dump
from towel.formatting import FormattingChangedCode
from towel.unification import argument_ownership
from towel.unification.argument_ownership import argument_handoff_plan, verify_argument_handoff
from towel.unification.models import ArgumentHandoff, RefactoringProposal
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer, type_parameter_names

SOURCE = """events=[]
class R:
    def __init__(self,name): self.name=name
    def __del__(self): events.append(self.name)
def f(a):
    local=R("local-f")
    return 11
def g(a):
    local=R("local-g")
    return 12
"""


def _argument_source(count: int, fail: bool) -> str:
    parameters = ",".join(chr(97 + index) for index in range(count))
    source = SOURCE.replace("f(a)", "f(" + parameters + ")").replace(
        "g(a)", "g(" + parameters + ")"
    )
    if fail:
        source = source.replace("return 11", 'raise ValueError("f")').replace(
            "return 12", 'raise ValueError("g")'
        )
    return source


def _effects(
    source: str, name: str, count: int = 1, fail: bool = False, *, starred: bool = False
) -> List[str]:
    namespace: dict[str, object] = {}
    exec(source, namespace)
    resource = cast(Callable[[str], object], namespace["R"])
    function = cast(Callable[..., object], namespace[name])
    # A starred call may retain its temporary argument tuple in the caller
    # through normal return (CPython 3.11). Direct calls isolate frame cleanup.
    assert 1 <= count <= 3
    try:
        if starred:
            function(*(resource(chr(97 + index)) for index in range(count)))
        elif count == 1:
            function(resource("a"))
        elif count == 2:
            function(resource("a"), resource("b"))
        else:
            function(resource("a"), resource("b"), resource("c"))
    except ValueError:
        assert fail
    return cast(List[str], namespace["events"])


def _proposal(
    tmp_path: Path, source: str, *, budget: int = 5
) -> tuple[UnificationRefactorEngine, RefactoringProposal]:
    path = tmp_path / "p.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, max_parameters=budget
    )
    proposals = [
        proposal
        for proposal in engine.analyze_file(str(path))
        if all(replacement.argument_handoff is not None for replacement in proposal.replacements)
    ]
    assert proposals
    return engine, proposals[0]


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("fail", [False, True])
def test_unused_arguments_release_in_original_order_before_helper_locals(
    tmp_path: Path, count: int, fail: bool
) -> None:
    source = _argument_source(count, fail)
    engine, proposal = _proposal(tmp_path, source)
    before = canonical_dump(proposal.extracted_function)
    calls = tuple(canonical_dump(replacement.node) for replacement in proposal.replacements)
    certificates = tuple(replacement.argument_handoff for replacement in proposal.replacements)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    assert canonical_dump(proposal.extracted_function) == before
    assert tuple(canonical_dump(replacement.node) for replacement in proposal.replacements) == calls
    assert (
        tuple(replacement.argument_handoff for replacement in proposal.replacements) == certificates
    )
    for name in ("f", "g"):
        expected = [chr(97 + index) for index in range(count)] + ["local-" + name]
        assert (
            _effects(source, name, count, fail) == _effects(output, name, count, fail) == expected
        )
    holder = next(
        node
        for node in ast.walk(ast.parse(output))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    assert holder.args.args[-1].arg.startswith("_towel_owner")
    assert holder.args.args[-1].annotation is None


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("fail", [False, True])
def test_starred_arguments_preserve_the_original_caller_owned_tuple_cleanup(
    tmp_path: Path, count: int, fail: bool
) -> None:
    source = _argument_source(count, fail)
    engine, proposal = _proposal(tmp_path, source)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    for name in ("f", "g"):
        # Preserve the real invocation's effects, including any references
        # owned by its temporary argument tuple; do not impose a frame-only order.
        assert _effects(source, name, count, fail, starred=True) == _effects(
            output, name, count, fail, starred=True
        )
    assert (tmp_path / "p.py").read_text() == source


@pytest.mark.parametrize(
    "body",
    [
        "a = None",
        "del a",
        "a += 1",
        "def inner():\n        nonlocal a\n        a = None",
        "read = lambda: a",
    ],
)
def test_rebound_deleted_nonlocal_and_parameter_cell_ownership_is_not_guessed(body: str) -> None:
    source = "def f(a):\n    " + body + "\n    return 1\n"
    module = ast.parse(source)
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    helper = ast.parse("def helper(): return 1").body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("return helper()").body[0]
    assert argument_handoff_plan(function, function.body, analyzer, call, helper) is None


def test_forged_metadata_and_stale_source_are_reproved(tmp_path: Path) -> None:
    engine, proposal = _proposal(tmp_path, SOURCE)
    replacement = proposal.replacements[0]
    plan = replacement.argument_handoff
    assert isinstance(plan, ArgumentHandoff)
    assert verify_argument_handoff(plan, SOURCE, replacement.node, proposal.extracted_function)
    assert not verify_argument_handoff(
        dataclasses.replace(plan, parameters=("invented",)),
        SOURCE,
        replacement.node,
        proposal.extracted_function,
    )
    assert not verify_argument_handoff(
        plan,
        SOURCE.replace("return 11", "return 13"),
        replacement.node,
        proposal.extracted_function,
    )
    proposal.replacements[0].argument_handoff = dataclasses.replace(plan, parameters=("invented",))
    with pytest.raises(RefactoringError, match="certificate"):
        engine.apply_refactoring(str(tmp_path / "p.py"), proposal)


def test_other_runtime_does_not_claim_cpython_cleanup_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(argument_ownership, "_supported_runtime", lambda: False)
    path = tmp_path / "p.py"
    path.write_text(SOURCE)
    engine = UnificationRefactorEngine(min_lines=2, reuse_existing_functions=False)
    assert not engine.analyze_file(str(path))


def test_rendered_parameter_budget_includes_ownership_holder(tmp_path: Path) -> None:
    engine, proposal = _proposal(tmp_path, SOURCE, budget=5)
    engine.unifier.max_parameters = len(proposal.extracted_function.args.args) + len(
        proposal.extracted_function.args.posonlyargs
    )
    with pytest.raises(RefactoringError, match="ownership parameter"):
        engine.apply_refactoring(str(tmp_path / "p.py"), proposal)


def test_observing_custom_finisher_preserves_the_transfer_proof(tmp_path: Path) -> None:
    engine, proposal = _proposal(tmp_path, SOURCE)
    observed: List[str] = []

    def observe(path: str, text: str) -> str:
        observed.append(text)
        return text

    engine.file_finisher = observe
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    assert observed == [output]
    assert _effects(output, "f") == _effects(SOURCE, "f")


@pytest.mark.parametrize("snippet", [False, True])
def test_mutating_custom_callback_is_called_then_refused(tmp_path: Path, snippet: bool) -> None:
    engine, proposal = _proposal(tmp_path, SOURCE)
    observed: List[str] = []

    def change(text: str) -> str:
        observed.append(text)
        return text.replace("_towel_arguments.pop()", "None")

    if snippet:
        engine.snippet_formatter = change
    else:
        engine.file_finisher = lambda path, text: change(text)
    with pytest.raises((RefactoringError, FormattingChangedCode), match="(meaning|handoff AST)"):
        engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    assert observed
    assert (tmp_path / "p.py").read_text() == SOURCE


def test_user_lambda_signature_and_escaping_generated_thunk_are_not_rewritten() -> None:
    module = ast.parse("def f(a,b):\n    return a.name\n")
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    helper = ast.parse("def helper(thunk):\n    return lambda: thunk()\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("return helper(lambda: a.name)").body[0]
    assert argument_handoff_plan(function, function.body, analyzer, call, helper) is None
    function.body = ast.parse("return (lambda: a.name)()").body
    module = ast.Module(body=[function], type_ignores=[])
    analyzer.analyze(module)
    helper = ast.parse("def helper(thunk): return thunk()").body[0]
    assert isinstance(helper, ast.FunctionDef)
    assert argument_handoff_plan(function, function.body, analyzer, call, helper) is None


def test_method_receiver_and_mixed_argument_kinds_keep_original_cleanup(tmp_path: Path) -> None:
    source = SOURCE[: SOURCE.index("def f(a):")] + """class C(R):
    def f(self,a,/,*rest,b,**kwargs):
        local=R("local-f")
        return 11
    def g(self,a,/,*rest,b,**kwargs):
        local=R("local-g")
        return 12
"""
    engine, proposal = _proposal(tmp_path, source, budget=8)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)

    def effects(text: str, name: str) -> List[str]:
        namespace: dict[str, object] = {}
        exec(text, namespace)
        resource = cast(Callable[[str], object], namespace["R"])
        receiver = cast(Callable[[str], object], namespace["C"])
        getattr(receiver("self"), name)(
            resource("a"), resource("rest"), b=resource("b"), k=resource("k")
        )
        return cast(List[str], namespace["events"])

    for name in ("f", "g"):
        assert effects(source, name) == effects(output, name)
        assert effects(output, name)[-1] == "local-" + name


def test_generated_thunk_captures_only_used_parameter_and_respects_nested_shadow() -> None:
    source = SOURCE[: SOURCE.index("def f(a):")] + """def f(a,b):
    local=R("local-f")
    return a.name + str([a for a in (1,2)])
"""
    module = ast.parse(source)
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    helper = ast.parse('def helper(thunk):\n    local=R("local-f")\n    return thunk()\n').body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("return helper(lambda: a.name + str([a for a in (1,2)]))").body[0]
    plan = argument_handoff_plan(function, function.body, analyzer, call, helper)
    assert plan is not None
    original = canonical_dump(call)
    replacement = argument_ownership.render_argument_handoff(call, plan, "helper")
    thunks = [node for node in ast.walk(replacement) if isinstance(node, ast.Lambda)]
    factory, thunk = thunks
    assert not factory.args.defaults
    assert len(factory.args.args) == 1
    assert not thunk.args.args and not thunk.args.defaults
    assert any(isinstance(node, ast.Name) and node.id == "a" for node in ast.walk(thunk.body))
    helper.args.args.append(ast.arg(arg="_owner"))
    function.body = replacement.body
    module.body.insert(2, helper)
    output = ast.unparse(ast.fix_missing_locations(module))
    assert canonical_dump(call) == original

    def effects(text: str) -> tuple[object, List[str]]:
        namespace: dict[str, object] = {}
        exec(text, namespace)
        resource = cast(Callable[[str], object], namespace["R"])
        function_call = cast(Callable[..., object], namespace["f"])
        value = function_call(resource("a"), resource("b"))
        return value, cast(List[str], namespace["events"])

    assert effects(source) == effects(output) == ("a[1, 2]", ["a", "b", "local-f"])


def test_compiler_facts_are_immutable_weak_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    import gc
    import weakref

    module = ast.parse("def f(a): return a\ndef g(b): return b")
    functions = [node for node in module.body if isinstance(node, ast.FunctionDef)]
    before = canonical_dump(module)
    compute = argument_ownership._compiled_codes
    calls: List[ast.AST] = []

    def counted(node: ast.AST) -> tuple[CodeType, ...]:
        calls.append(node)
        return compute(node)

    monkeypatch.setattr(argument_ownership, "_compiled_codes", counted)
    for function in functions:
        assert argument_ownership._code(module, function) is not None
    assert len(calls) == 1
    assert canonical_dump(module) == before
    reference = weakref.ref(module)
    calls.clear()
    del module
    gc.collect()
    assert reference() is None


def test_unannotated_holder_keeps_mypy_unchecked_body_semantics(tmp_path: Path) -> None:
    from towel.type_inference import CheckSuccess, MypyInferrer

    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\ncheck_untyped_defs = false\n")
    source = (
        SOURCE.replace("events=[]", "events: list[str]=[]")
        .replace("return 11", "return opaque.missing(11)")
        .replace("return 12", "return opaque.missing(12)")
    )
    engine, proposal = _proposal(tmp_path, source)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    module = ast.parse(output)
    helper = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    assert all(
        argument.annotation is None for argument in helper.args.posonlyargs + helper.args.args
    )
    helper.args.args[-1].annotation = ast.Constant("object")
    erroneously_checked = ast.unparse(ast.fix_missing_locations(module))
    path = str(tmp_path / "p.py")
    oracle = MypyInferrer()
    try:
        for text in (source, output):
            verdict = oracle.check_project({path: text})
            assert isinstance(verdict, CheckSuccess) and not verdict.errors
        contrary = oracle.check_project({path: erroneously_checked})
        assert isinstance(contrary, CheckSuccess)
        assert any("opaque" in error.message for error in contrary.errors)
    finally:
        oracle.close()


def test_import_alias_cannot_overwrite_the_argument_owner(tmp_path: Path) -> None:
    source = (
        SOURCE[: SOURCE.index("def f(a):")].replace(
            "def __init__(self,name): self.name=name",
            'def __init__(self,name): self.name=name; events.append("born-"+name)',
        )
        + """def f(a):
    inside=R("inside")
    import math as _towel_owner
    local=R("local")
    return 11

def g(a):
    inside=R("inside")
    import math as _towel_owner
    local=R("local")
    return 11
"""
    )
    engine, proposal = _proposal(tmp_path, source)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    expected = ["born-a", "born-inside", "born-local", "a", "inside", "local"]
    assert _effects(source, "f") == _effects(output, "f") == expected
    assert _effects(source, "g") == _effects(output, "g") == expected
    helper = next(
        node
        for node in ast.parse(output).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    assert helper.args.args[-1].arg == "_towel_owner_1"


@pytest.mark.parametrize(
    "binder",
    [
        "import math as NAME",
        "from math import pi as NAME",
        "def NAME(): pass",
        "class NAME: pass",
        "try:\n    pass\nexcept Exception as NAME:\n    pass",
        "match None:\n    case NAME:\n        pass",
        "global NAME",
        "def inner():\n    nonlocal NAME",
    ],
)
def test_ownership_generated_names_reserve_every_lexical_binder(binder: str) -> None:
    from towel.unification.function_scope import identifiers

    for stem in ("_towel_owner", "_towel_arguments", "_towel_argument"):
        module = ast.parse(binder.replace("NAME", stem))
        taken = set(identifiers((module,)))
        assert argument_ownership._fresh(stem, taken) == stem + "_1"


def test_certificate_box_and_capture_names_avoid_unused_module_binders(tmp_path: Path) -> None:
    source = "import math as _towel_arguments\ndef _towel_argument(): pass\n" + SOURCE
    engine, proposal = _proposal(tmp_path, source)
    for replacement in proposal.replacements:
        plan = replacement.argument_handoff
        assert plan is not None
        assert plan.box_name == "_towel_arguments_1"
        assert plan.captures == (("a", "_towel_argument_1"),)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    assert _effects(source, "f") == _effects(output, "f")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="native generic syntax")
@pytest.mark.parametrize("fail", [False, True])
def test_native_generic_header_keeps_resource_parameter_cleanup(tmp_path: Path, fail: bool) -> None:
    source = SOURCE[: SOURCE.index("def f(a):")] + """def f[T](a: T):
    local=R("local-f")
    return 11

def g[U](a: U):
    local=R("local-g")
    return 11
"""
    if fail:
        source = source.replace("return 11", 'raise ValueError("stop")')
    engine, proposal = _proposal(tmp_path, source)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    for name in ("f", "g"):
        assert (
            _effects(source, name, fail=fail)
            == _effects(output, name, fail=fail)
            == ["a", "local-" + name]
        )
    originals = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)]
    rendered = [
        n for n in ast.parse(output).body if isinstance(n, ast.FunctionDef) and n.name in {"f", "g"}
    ]
    assert [canonical_dump(n.args) for n in originals] == [canonical_dump(n.args) for n in rendered]
    assert [type_parameter_names(n) for n in originals] == [
        type_parameter_names(n) for n in rendered
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="native generic syntax")
@pytest.mark.parametrize(
    "body",
    [
        "return T",
        "return (lambda: T)()",
        "def inner(x:T): return x\n    return items[0]",
    ],
)
def test_runtime_native_type_parameter_reads_are_not_transferred(body: str) -> None:
    module = ast.parse("def f[T](items:list[T])->T:\n    " + body + "\n")
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    helper = ast.parse("def helper(items): return items[0]").body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("return helper(items)").body[0]
    assert argument_handoff_plan(function, function.body, analyzer, call, helper) is None


@pytest.mark.parametrize(
    "parameters",
    [("a",), ("name", "a_rather_long_parameter_name", "another_long_parameter_name")],
)
def test_black_keeps_independent_parameter_deletes_and_cleanup_order(
    tmp_path: Path, parameters: tuple[str, ...]
) -> None:
    from towel.formatting import BlackSettings, black_formatter

    signature = ", ".join(parameters)
    source = SOURCE.replace("f(a)", f"f({signature})").replace("g(a)", f"g({signature})")
    engine, proposal = _proposal(tmp_path, source)
    formatter = black_formatter(BlackSettings(line_length=60))
    engine.snippet_formatter = formatter
    for replacement in proposal.replacements:
        plan = replacement.argument_handoff
        assert plan is not None and plan.parameters == parameters
        prefix = argument_ownership.render_argument_handoff(
            replacement.node, plan, proposal.extracted_function.name
        )
        assignment, *deletions, call = prefix.body
        assert isinstance(assignment, ast.Assign)
        assert len(deletions) == len(parameters)
        assert all(isinstance(deletion, ast.Delete) for deletion in deletions)
        deleted = [
            target.id
            for deletion in deletions
            if isinstance(deletion, ast.Delete)
            for target in deletion.targets
            if isinstance(target, ast.Name)
        ]
        assert deleted == list(parameters)
        assert all(
            isinstance(deletion, ast.Delete) and len(deletion.targets) == 1
            for deletion in deletions
        )
        formatted = formatter(ast.unparse(prefix))
        assert canonical_dump(ast.parse(formatted)) == canonical_dump(prefix)
        assert isinstance(call, ast.Return)
    output = engine.apply_refactoring(str(tmp_path / "p.py"), proposal)
    for name in ("f", "g"):
        assert _effects(source, name, len(parameters)) == _effects(output, name, len(parameters))
        assert _effects(output, name, len(parameters)) == [
            *(chr(97 + index) for index in range(len(parameters))),
            "local-" + name,
        ]


def test_analysis_reserves_the_final_holder_in_the_parameter_budget(tmp_path: Path) -> None:
    _, proposal = _proposal(tmp_path, SOURCE)
    path = tmp_path / "p.py"
    semantic_count = len(proposal.extracted_function.args.posonlyargs) + len(
        proposal.extracted_function.args.args
    )
    refused = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, max_parameters=semantic_count
    )
    assert refused.analyze_file(str(path)) == []
    assert "ownership_parameter_budget" in refused.declined_pairs
    admitted = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, max_parameters=semantic_count + 1
    )
    candidates = admitted.analyze_file(str(path))
    assert candidates
    for candidate in candidates:
        output = admitted.apply_refactoring(str(path), candidate)
        assert _effects(SOURCE, "f") == _effects(output, "f")
        assert _effects(SOURCE, "g") == _effects(output, "g")
    assert path.read_text() == SOURCE


SUPER_CAPTURED_RECEIVER = """events=[]
class Base:
    def label(self,n): return str(n)
class C(Base):
    @property
    def alpha(self): return "A"
    @property
    def beta(self): return "B"
    def first(self,n,transform=str.upper):
        events.append('head')
        text=super().label(n)
        events.append(self.alpha)
        return transform(text)
    def second(self,n,transform=str.lower):
        events.append('head')
        text=super().label(n)
        events.append(self.beta)
        return transform(text)
"""


def test_method_budget_includes_implicit_super_receiver_and_holder(tmp_path: Path) -> None:
    """Receiver-free syntax still requires the original frame's first argument for super()."""
    path = tmp_path / "p.py"
    path.write_text(SUPER_CAPTURED_RECEIVER)
    refused = UnificationRefactorEngine(
        min_lines=3, max_parameters=4, reuse_existing_functions=False
    )
    assert refused.analyze_file(str(path)) == []
    assert "ownership_parameter_budget" in refused.declined_pairs
    engine, proposal = _proposal(tmp_path, SUPER_CAPTURED_RECEIVER, budget=5)
    assert proposal.insert_into_class == "C"
    output = engine.apply_refactoring(str(path), proposal)
    helper = next(
        node
        for node in ast.walk(ast.parse(output))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    parameters = [argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)]
    assert parameters[0] == "self" and len(parameters) == 5
    assert any(name.startswith("_towel_owner") for name in parameters)

    def observe(text: str) -> tuple[object, object, object]:
        namespace: dict[str, object] = {}
        exec(text, namespace)
        klass = cast(Callable[[], object], namespace["C"])
        first = getattr(klass(), "first")
        second = getattr(klass(), "second")
        return first(1), second(2), namespace["events"]

    assert observe(output) == observe(SUPER_CAPTURED_RECEIVER)
    # A proposal prepared under a larger budget cannot bypass the final guard.
    engine.unifier.max_parameters = 4
    with pytest.raises(RefactoringError, match="final helper.*ownership parameter"):
        engine.apply_refactoring(str(path), proposal)
