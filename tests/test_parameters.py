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

"""The shared parameter enumerators must cover every argument category.

refactor_engine._collect_parameter_names, binding_detector._bind_function_parameters,
scope_analyzer, unifier, definite_assignment, and assignment_analyzer all rely on
these, so a gap here would silently drop bindings across the analysis.
"""

import ast

from towel.unification.parameters import parameter_names, parameter_nodes


def _args(src: str) -> ast.arguments:
    function = ast.parse(src).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function.args


def test_parameter_names_covers_every_category():
    args = _args("def f(a, /, b, c=1, *args, d, e=2, **kw): pass")
    assert set(parameter_names(args)) == {"a", "b", "c", "args", "d", "e", "kw"}


def test_parameter_nodes_yields_in_declaration_group_order():
    args = _args("def f(a, /, b, *args, c, **kw): pass")
    # positional-only, positional, keyword-only, *args, **kwargs
    assert [node.arg for node in parameter_nodes(args)] == ["a", "b", "c", "args", "kw"]


def test_parameter_names_empty_for_no_arguments():
    assert set(parameter_names(_args("def f(): pass"))) == set()
