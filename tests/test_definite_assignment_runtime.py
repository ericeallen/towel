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

"""A name the definite-assignment analysis calls bound before a statement is bound whenever it runs.

``definitely_bound_before`` decides which of a block's free variables the
call site may read eagerly (``available_argument_names``). A name it calls
bound that is not makes the eager argument raise before the block's first
effect, where the block raised only at its read: a ``del`` later in a loop
body unbinds the name for the next iteration, and a handler or ``finally``
may start after a ``del`` in the ``try``. This test runs what it analyses:
the functions of the observability test's generator, with a probe before
every statement at every depth that records which locals are bound there,
called many times on random inputs. Every name the analysis claims before a
statement must be bound every time the statement is reached.
"""

from __future__ import annotations

import ast
import random
from typing import Callable, Dict, List, Set, Tuple

from tests.test_renamed_binder_observability_runtime import E, _environment, _Writer
from towel.unification.definite_assignment import definitely_bound_before

SEED = 1773
FUNCTIONS = 600
RUNS = 12
_FIELDS = ("body", "orelse", "finalbody")


def _instrument(function: ast.FunctionDef) -> Dict[int, ast.stmt]:
    """Put ``probe(n, locals())`` before every statement; the statement each ``n`` precedes."""
    probed: Dict[int, ast.stmt] = {}

    def instrument(statements: List[ast.stmt]) -> None:
        index = 0
        while index < len(statements):
            statement = statements[index]
            number = len(probed)
            probed[number] = statement
            probe = ast.parse(f"probe({number}, locals())").body[0]
            statements.insert(index, probe)
            index += 2
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # another scope, with locals of its own
            for field in _FIELDS:
                inner = getattr(statement, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    instrument(inner)
            for handler in getattr(statement, "handlers", []):
                instrument(handler.body)
            for case in getattr(statement, "cases", []):
                instrument(case.body)

    instrument(function.body)
    return probed


def test_definitely_bound_names_are_bound_whenever_the_statement_runs() -> None:
    rng = random.Random(SEED)
    checked = 0
    probes = 0
    while checked < FUNCTIONS:
        body = _Writer(rng).block(0, "    ")
        source = "\n".join(["def f(p, seen):", *body, "    return seen", ""])
        try:
            tree = ast.parse(source)
            compile(tree, "<generated>", "exec")
        except SyntaxError:
            continue
        function_node = tree.body[0]
        assert isinstance(function_node, ast.FunctionDef)
        probed = _instrument(function_node)
        claimed = {
            number: definitely_bound_before(function_node, statement)
            for number, statement in probed.items()
        }
        code = compile(ast.fix_missing_locations(tree), "<generated>", "exec")
        checked += 1
        for _ in range(RUNS):
            reached: List[Tuple[int, Set[str]]] = []
            namespace = _environment(random.Random(rng.random()))
            namespace["probe"] = lambda number, bound: reached.append((number, set(bound)))
            exec(code, namespace)  # nosec B102: the test's own generated code
            function: Callable[[List[int], List[object]], object] = namespace["f"]  # type: ignore[assignment]
            try:
                function([rng.randrange(3) for _ in range(rng.randrange(3))], [])
            except (E, TypeError, AssertionError, UnboundLocalError, NameError):
                pass
            for number, bound in reached:
                probes += 1
                missing = claimed[number] - bound
                assert not missing, (
                    f"{sorted(missing)} claimed bound before statement {number}, unbound there\n"
                    f"{ast.unparse(probed[number])}\n--- in ---\n{source}"
                )
    assert probes > FUNCTIONS * RUNS
