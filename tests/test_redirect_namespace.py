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

"""Integration 6: rebinding shapes the redirect review did not try.

Each mutation replaces the module's ``total`` before ``report`` runs. The
original ``report`` never calls ``total``; a redirected one would, so any
redirect here changes the program's output.
"""

import ast
from pathlib import Path

import pytest

from tests.test_callback_parameters import run_script
from tests.test_redirect_rebinding import STATEMENT_PAIR
from tests.test_shape_review import dry
from towel.unification.rebinding import rebinding_reason

REPLACEMENT = "lambda value: print(-999)"

# A namespace mapping, or the function that returns one, escaping as a value.
NAMESPACE_ESCAPES = {
    "globals_function_alias": "namespace = globals\nnamespace()['total'] = REPLACEMENT\n",
    "globals_passed_to_dict_setitem": "dict.__setitem__(globals(), 'total', REPLACEMENT)\n",
    "globals_passed_to_function": "def poke(ns):\n    ns['total'] = REPLACEMENT\npoke(globals())\n",
    "globals_lambda_default": "(lambda ns=globals(): ns.__setitem__('total', REPLACEMENT))()\n",
    "globals_in_container": "[globals()][0]['total'] = REPLACEMENT\n",
    "globals_bound_method": "store = globals().__setitem__\nstore('total', REPLACEMENT)\n",
    "globals_dict_init": "globals().__init__(total=REPLACEMENT)\n",
    "builtins_globals_attribute": "import builtins\nbuiltins.globals()['total'] = REPLACEMENT\n",
    "builtins_globals_by_name": "import builtins\ngetattr(builtins, 'globals')()['total'] = REPLACEMENT\n",
}

# A module namespace reached through a function's or a frame's attribute.
FUNCTION_NAMESPACES = {
    "function_globals_subscript": "report.__globals__['total'] = REPLACEMENT\n",
    "function_globals_update": "report.__globals__.update(total=REPLACEMENT)\n",
    "frame_globals": "__import__('sys')._getframe().f_globals['total'] = REPLACEMENT\n",
    "frame_locals_at_module": "__import__('sys')._getframe().f_locals['total'] = REPLACEMENT\n",
}

MUTATIONS = {**NAMESPACE_ESCAPES, **FUNCTION_NAMESPACES}


def _scan(source: str) -> str | None:
    target = ast.parse(source).body[0]
    assert isinstance(target, ast.FunctionDef) and target.name == "total"
    return rebinding_reason({"mod.py": source}, "mod.py", target)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_rebinding_preserves_statement_duplicate(
    tmp_path: Path, strict: bool, mutation: str
) -> None:
    """Integration 6: each rebinding keeps a fresh helper and the original output."""
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text(
        STATEMENT_PAIR + MUTATIONS[mutation].replace("REPLACEMENT", REPLACEMENT) + "report(2)\n"
    )
    before = run_script(source)
    assert before == (0, "before\n3\n4\n5\nafter 2\n", "")
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before
    assert "def __extracted_func_" in output.read_text()


@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_rebinding_is_located(mutation: str) -> None:
    """Integration 6: the scan names the line of each rebinding shape."""
    source = STATEMENT_PAIR + MUTATIONS[mutation].replace("REPLACEMENT", REPLACEMENT)
    reason = _scan(source)
    assert reason is not None and "mod.py:" in reason and "may rebind" in reason


@pytest.mark.parametrize(
    "use",
    [
        "print(globals()['other'])",
        "print('other' in globals())",
        "print(sorted(globals().keys()))",
        "print(globals().get('other'))",
        "for name in globals():\n    pass",
        "print([name for name, value in globals().items()])",
        "globals()['other'] = 1",
        "def helper(namespace=None):\n    return namespace",
    ],
)
def test_namespace_reads_do_not_block_redirect(use: str) -> None:
    """Integration 6 control: reading the namespace, or writing another key, is no rebinding."""
    assert _scan(STATEMENT_PAIR + use + "\n") is None
