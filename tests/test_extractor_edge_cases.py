import ast
from typing import Set, Dict

from src.towel.unification.extractor import HygienicExtractor
from src.towel.unification.unifier import Substitution


def make_substitution(param_map):
    """Utility to build a Substitution from a dict{param: (block_idx, expr_code)...}."""
    subst = Substitution()
    for param_name, entries in param_map.items():
        for block_idx, code in entries:
            expr = ast.parse(code, mode="eval").body
            subst.add_mapping(block_idx, expr, param_name)
    return subst


def test_extract_function_injects_global_nonlocal_and_multi_return():
    block = ast.parse(
        """
value = a + b
other = value * 2
"""
    ).body
    subst = make_substitution({"__param_0": [(0, "a"), (1, "x")], "__param_1": [(0, "b"), (1, "y")]})
    extractor = HygienicExtractor()
    func_def, param_order = extractor.extract_function(
        template_block=block,
        substitution=subst,
        free_variables={"free1", "free2"},
        enclosing_names={"extracted_function", "free1"},  # force rename of function name
        is_value_producing=True,
        return_variables=["value", "other"],
        global_decls={"G"},
        nonlocal_decls={"N"},
        function_name="extracted_function",
    )
    # Function should be renamed to avoid collision
    assert func_def.name.startswith("__extracted_function_"), func_def.name
    # Global + Nonlocal declarations first
    assert isinstance(func_def.body[0], ast.Global)
    assert isinstance(func_def.body[1], ast.Nonlocal)
    # Multi-var return tuple last
    assert isinstance(func_def.body[-1], ast.Return)
    ret = func_def.body[-1].value
    assert isinstance(ret, ast.Tuple)
    returned_ids = [elt.id for elt in ret.elts]
    assert set(returned_ids) == {"value", "other"}
    # Parameters include unified first then free variables sorted
    assert [a.arg for a in func_def.args.args][:2] == ["__param_0", "__param_1"]
    assert set(a.arg for a in func_def.args.args[2:]) == {"free1", "free2"}


def test_generate_call_params_used_as_callee_wrapped():
    # Blocks differ only in callee name -> parameterized; callee becomes __param_0 used as callee
    block = ast.parse("result = foo(a)").body
    subst = Substitution()
    # Parameterize callee names across two synthetic blocks
    subst.add_mapping(0, ast.Name(id="foo"), "__param_0")
    subst.add_mapping(1, ast.Name(id="bar"), "__param_0")
    # Also parameterize argument so ordering is stable
    subst.add_mapping(0, ast.Name(id="a"), "__param_1")
    subst.add_mapping(1, ast.Name(id="a"), "__param_1")

    extractor = HygienicExtractor()
    func_def, param_order = extractor.extract_function(
        template_block=block,
        substitution=subst,
        free_variables=set(),
        enclosing_names=set(),
        is_value_producing=True,
        return_variables=["result"],
    )

    # params_used_as_callee should contain __param_0
    assert "__param_0" in subst.params_used_as_callee

    call_stmt = extractor.generate_call(
        function_name=func_def.name,
        block_idx=0,
        substitution=subst,
        param_order=param_order,
        free_variables=set(),
        is_value_producing=True,
        return_variables=["result"],
    )
    assert isinstance(call_stmt, ast.Assign)
    call = call_stmt.value
    assert isinstance(call, ast.Call)
    # Argument for __param_0 should be a lambda with *args, **kwargs forwarding
    # Determine ordering
    ordered_params = sorted(param_order.items(), key=lambda kv: kv[1])
    arg_for_callee = call.args[[name for name, _ in ordered_params].index("__param_0")]
    assert isinstance(arg_for_callee, ast.Lambda)
    assert arg_for_callee.vararg.arg == "args"
    assert arg_for_callee.kwarg.arg == "kwargs"


def test_generate_call_function_param_lambda_lift():
    # Expression referencing a bound variable -> function param
    # Simulate param referencing loop var 'i' available at call site
    subst = Substitution()
    expr = ast.parse("i + 1", mode="eval").body
    subst.add_mapping(0, expr, "__param_0", bound_vars=["i"])  # mark as function param
    extractor = HygienicExtractor()
    block = ast.parse("result = i + 1").body
    func_def, param_order = extractor.extract_function(
        block, subst, free_variables={"i"}, enclosing_names=set(), is_value_producing=True, return_variables=["result"]
    )
    call_stmt = extractor.generate_call(
        func_def.name, 0, subst, param_order, free_variables={"i"}, is_value_producing=True, return_variables=["result"]
    )
    call = call_stmt.value
    assert isinstance(call, ast.Call)
    # The unified function param should be passed a lambda? No, generate_call wraps lambda only for params_used_as_callee or function params with bound variables
    arg0 = call.args[param_order["__param_0"]]
    # Function parameter should be passed as lambda capturing bound vars
    assert isinstance(arg0, ast.Lambda), f"Expected lambda for function param, got {type(arg0)}"
    assert [a.arg for a in arg0.args.args] == ["i"]


def test_generate_call_aug_assign_mapping():
    # Simulate substitution carrying aug_assign_mappings for identifier variation
    subst = Substitution()
    subst.aug_assign_mappings = {"__param_2": {0: "total", 1: "output"}}
    # Provide parameter expressions for unified params 0 & 1; param2 is free variable so not in param_expressions
    subst.add_mapping(0, ast.Name(id="a"), "__param_0")
    subst.add_mapping(1, ast.Name(id="x"), "__param_0")
    subst.add_mapping(0, ast.Name(id="b"), "__param_1")
    subst.add_mapping(1, ast.Name(id="y"), "__param_1")
    extractor = HygienicExtractor()
    block = ast.parse("total = a + b\nresult = total * 2").body
    func_def, param_order = extractor.extract_function(
        block,
        subst,
        free_variables={"__param_2"},  # treat as free variable name
        enclosing_names=set(),
        is_value_producing=True,
        return_variables=["result"],
    )
    call_stmt = extractor.generate_call(
        func_def.name,
        1,  # second block index uses mapping to 'output'
        subst,
        param_order,
        free_variables={"__param_2"},
        is_value_producing=True,
        return_variables=["result"],
    )
    call = call_stmt.value
    # Find argument corresponding to free variable '__param_2'
    free_param_idx = param_order["__param_2"]
    arg = call.args[free_param_idx]
    assert isinstance(arg, ast.Name)
    assert arg.id == "output"


def test_ensure_unique_name_collision():
    extractor = HygienicExtractor()
    name1 = extractor._ensure_unique_name("process", {"process"})
    name2 = extractor._ensure_unique_name("process", {"process"})
    assert name1 != "process"
    assert name2 != name1