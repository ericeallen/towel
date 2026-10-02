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

"""Only operations that consume instrumentation facts need rebinding proofs."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.decorator_reach import _Module, _Resolver, _Slot
from towel.unification.import_graph import ImportGraphCache
from towel.unification.instrumentation_flow import UNKNOWN, Value, ValueKind


def _subject(root: Path, source: str) -> tuple[_Resolver, _Module]:
    (root / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    path = root / "subject.py"
    path.write_text(source)
    resolver = _Resolver(ImportGraphCache())
    module = resolver._load(str(path))
    assert module is not None
    return resolver, module


@pytest.mark.parametrize(
    "source",
    ["import math\nmath.sin(1)\n", "compile(1, '', 'exec')\n"],
    ids=["unrecognized-operation", "recognized-operation-without-tracked-source"],
)
def test_irrelevant_operations_do_not_request_namespace_rebinding(
    tmp_path: Path, source: str
) -> None:
    """Resolving ordinary calls must not demand the project's namespace-write scan."""
    resolver, module = _subject(tmp_path, source)
    with patch.object(resolver, "_origin_rebound", wraps=resolver._origin_rebound) as rebound:
        returned, found = resolver._flow(module.tree.body, _Slot(None, 0, True), module, {})
    assert returned == UNKNOWN
    assert found == ()
    assert rebound.call_count == 0


@pytest.mark.parametrize(
    "rebound", [False, True], ids=["stable-alternative", "rebound-alternative"]
)
def test_compilation_keeps_the_existing_diagnostic_for_every_stable_origin(
    tmp_path: Path, rebound: bool
) -> None:
    """Even an unrecognized alternative participates in the established diagnostic order."""
    if rebound:
        (tmp_path / "patches.py").write_text("import builtins\nbuiltins.abs = 1\n")
    resolver, module = _subject(
        tmp_path, "operation = compile if condition else abs\noperation(source, '', 'exec')\n"
    )
    affected = frozenset({(module.path, "C.f")})
    source = Value(frozenset({ValueKind.SOURCE}), targets=affected)
    _, found = resolver._flow(module.tree.body, _Slot(None, 0, True), module, {"source": source})
    assert tuple((name, call.lineno, targets) for name, call, targets in found) == (
        ("builtins.compile" if rebound else "builtins.abs", 2, affected),
    )


def test_factory_calls_and_project_helpers_keep_the_instrumented_target(tmp_path: Path) -> None:
    """A call without tracked arguments can create the instrumenter used by a helper."""
    resolver, module = _subject(
        tmp_path,
        "from typeguard import typechecked as checked\n"
        "def apply(value):\n    return checked()(value)\n"
        "class C:\n    def f(self): return 1\n"
        "apply(C)\n",
    )
    _, found = resolver._flow(module.tree.body, _Slot(None, 0, True), module, {})
    assert tuple((name, call.lineno, targets) for name, call, targets in found) == (
        ("typeguard.typechecked", 3, frozenset({(module.path, "C")})),
    )


def test_values_without_symbols_retain_their_existing_provenance(tmp_path: Path) -> None:
    resolver, module = _subject(tmp_path, "def carry(value):\n    return value\n")
    function = module.tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    value = Value(frozenset({ValueKind.METHOD}), targets=frozenset({(module.path, "C.f")}))
    returned, found = resolver._flow(
        function.body, _Slot(function, 0, True), module, {"value": value}
    )
    assert returned == value
    assert found == ()
