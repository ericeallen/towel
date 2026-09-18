import ast
from typing import Any

import pytest
import textwrap

from towel.unification.refactor_engine import UnificationRefactorEngine


def _engine(
    min_lines: int = 2, *, promote_equal_hof_literals: bool = False
) -> UnificationRefactorEngine:
    return UnificationRefactorEngine(
        max_parameters=5,
        min_lines=min_lines,
        parameterize_constants=True,
        promote_equal_hof_literals=promote_equal_hof_literals,
    )


def test_module_level_helper_call_from_class_uses_direct_name(tmp_path):
    # Two unrelated classes with identical instance methods force module-level helper extraction.
    code = """
    class A:
        def m(self, x):
            y = x + 1
            z = y * 2
            return z

    class B:
        def m(self, x):
            y = x + 1
            z = y * 2
            return z
    """
    engine = _engine(min_lines=2)
    file_path = tmp_path / "mod.py"
    file_path.write_text(textwrap.dedent(code).strip() + "\n", encoding="utf-8")
    proposals = engine.analyze_files([str(file_path)])
    assert (
        proposals
    ), "Expected at least one proposal for identical methods across unrelated classes"
    new_src = engine.apply_refactoring(str(file_path), proposals[0])

    # Helper should be defined at module level with standard name
    assert "def _extracted_func_" in new_src

    # Calls inside class methods must use the same unmangled module name.
    assert "return _extracted_func_" in new_src
    namespace: dict[str, Any] = {}
    exec(new_src, namespace)
    assert namespace["A"]().m(3) == 8
    assert namespace["B"]().m(3) == 8


def test_undefined_name_validation_blocks_brittle_pipeline(tmp_path):
    # One function binds 'filtered'; the other inlines the filter. A naïve refactor could
    # generate a call-site that references 'filtered' where it doesn't exist; engine should skip.
    src = """
    def a(data):
        filtered = filter(lambda x: x > 0, data)
        result = list(map(lambda x: x * 2, filtered))
        return result

    def b(data):
        result = list(map(lambda x: x * 2, filter(lambda x: x > 0, data)))
        return result
    """
    engine = _engine(min_lines=2)
    file_path = tmp_path / "mod.py"
    file_path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    proposals = engine.analyze_files([str(file_path)])
    # Expect either no proposals or only proposals that don't create brittle call-sites
    # We assert conservatively that the engine found nothing safe to extract here.
    assert proposals == []


def test_option_b_promotes_equal_literals_in_higher_order_factories(tmp_path):
    # Equal literals in make_validator calls should be promoted and threaded as parameters
    # into the extracted helper (Option B policy).
    src = """
    def c(data):
        def make_validator(limit):
            return lambda x: x > limit
        validator = make_validator(5)
        return list(filter(validator, data))

    def d(data):
        def make_validator(limit):
            return lambda x: x > limit
        validator = make_validator(5)
        return list(filter(validator, data))
    """
    # Enable Option B promotion explicitly for this test
    engine = _engine(min_lines=2, promote_equal_hof_literals=True)
    file_path = tmp_path / "mod.py"
    file_path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    props = engine.analyze_files([str(file_path)])
    assert props, "Expected a proposal for identical higher-order patterns"
    out = engine.apply_refactoring(str(file_path), props[0])

    # Extracted helper should take a parameter (e.g., __param_0) and use it when calling make_validator
    mod = ast.parse(out)
    fn_defs = [
        n
        for n in mod.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
    ]
    assert fn_defs, "Extracted helper not found"
    helper = fn_defs[0]
    # There should be at least one parameter
    assert helper.args.args, "Helper should expose parameters after promotion"

    # Helper body should include a call make_validator(__param_X)
    calls = [
        n
        for n in ast.walk(helper)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "make_validator"
    ]
    assert calls, "Helper should call make_validator"
    called_with_param = any(
        isinstance(c.args[0], ast.Name) and c.args[0].id.startswith("__param_")
        for c in calls
        if c.args
    )
    assert called_with_param, "make_validator should receive a promoted parameter argument"

    # And ensure literal 5 was removed from helper body (threaded as parameter instead)
    literals = [node.value for node in ast.walk(helper) if isinstance(node, ast.Constant)]
    assert 5 not in literals, "Literal 5 should be threaded as a parameter when promotion enabled"


def test_option_b_disabled_keeps_equal_literals_inline(tmp_path):
    src = """
    def c(data):
        def make_validator(limit):
            return lambda x: x > limit
        validator = make_validator(5)
        return list(filter(validator, data))

    def d(data):
        def make_validator(limit):
            return lambda x: x > limit
        validator = make_validator(5)
        return list(filter(validator, data))
    """
    engine = _engine(min_lines=2, promote_equal_hof_literals=False)
    file_path = tmp_path / "mod.py"
    file_path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    props = engine.analyze_files([str(file_path)])
    assert props, "Expected a proposal for identical higher-order patterns"
    out = engine.apply_refactoring(str(file_path), props[0])

    mod = ast.parse(out)
    helpers = [
        n
        for n in mod.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
    ]
    assert helpers, "Helper should exist"
    helper = helpers[0]
    # With Option B disabled, literal 5 stays inline in helper body
    literals = [node.value for node in ast.walk(helper) if isinstance(node, ast.Constant)]
    assert 5 in literals, "Literal 5 should remain inline when promotion disabled"

    # Helper should not introduce extra parameters beyond the data argument
    assert all(arg.arg != "__param_0" for arg in helper.args.args)


def test_promotion_failure_is_a_bug_not_a_warning():
    """An error inside literal promotion propagates.

    Promotion used to catch four exception types, warn, and roll the
    substitution back, which turned a defect in the promotion code into a
    silently degraded proposal. A bug surfaces now.
    """
    import ast
    from unittest.mock import patch

    from towel.unification.unifier import Unifier

    src = "a = helper(1)\nb = a + 1\n"
    blocks = [ast.parse(src).body, ast.parse(src).body]

    def boom(self, _blocks, subst):  # patched as a method: takes self
        raise TypeError("promotion blew up")

    unifier = Unifier(max_parameters=5, promote_equal_hof_literals=True)
    with patch.object(Unifier, "_promote_hof_literals", boom):
        with pytest.raises(TypeError, match="promotion blew up"):
            unifier.unify_blocks([b[:] for b in blocks], [{}, {}])
