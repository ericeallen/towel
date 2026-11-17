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

"""Review item 5: ordinary correctness remains covered when ownership tests run strict."""

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests.relaxed_early_cases import CASES, Case
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.models import MethodKind

pytestmark = pytest.mark.relaxed_mode


def _observe(path: Path) -> tuple[int, str, str]:
    run = subprocess.run(
        [sys.executable, "-B", str(path)], capture_output=True, text=True, timeout=20
    )
    return run.returncode, run.stdout, run.stderr


def _preserved(tmp_path: Path, source: str, driver: str = "") -> int:
    path = tmp_path / "subject.py"
    source = textwrap.dedent(source)
    path.write_text(source + "\n" + driver)
    before = _observe(path)
    assert before[0] == 0 and not before[2], before
    path.write_text(source)
    engine = UnificationRefactorEngine(
        strict=False, min_lines=2, max_parameters=8, skip_trivial_helpers=False
    )
    final, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied > 0, engine.declined_pairs
    path.write_text(final + "\n" + driver)
    assert _observe(path) == before
    return applied


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_original_strict_program_preserves_behavior(tmp_path: Path, case: Case) -> None:
    if (
        sys.version_info < (3, 12)
        and case.name == "test_partial_generic_function_bodies_keep_type_parameter_bindings"
    ):
        pytest.skip("PEP 695 syntax requires Python 3.12")
    assert _preserved(tmp_path, case.source, case.driver) > 0


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax requires Python 3.12")
@pytest.mark.parametrize("declaration", ["T", "*T", "**T"])
@pytest.mark.parametrize("site_count", [2, 3])
def test_generic_function_type_parameter_identity(
    tmp_path: Path, declaration: str, site_count: int
) -> None:
    names = ("first", "second", "third")[:site_count]
    source = "\n".join(
        f"def {name}[{declaration}](x):\n    a = T\n    b = x + 1\n    c = b * 3\n    return a, c\n"
        for name in names
    )
    driver = "\n".join(f"print({name}(1)[0] is {name}.__type_params__[0])" for name in names)
    _preserved(tmp_path, source, driver)


@pytest.mark.parametrize("name", ["len", "value"])
def test_unbound_lexical_local(tmp_path: Path, name: str) -> None:
    source = f"""value = 100
def first(flag):
    if flag:
        {name} = 7
    if flag:
        a = {name} + 1
    else:
        a = 5
    b = a * 2
    c = b - 3
    return c

def second(flag):
    for index in range(int(flag)):
        {name} = 8
    if flag:
        a = {name} + 1
    else:
        a = 5
    b = a * 2
    c = b - 3
    return c
"""
    _preserved(tmp_path, source, "print(first(False), second(False), first(True), second(True))")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax requires Python 3.12")
@pytest.mark.parametrize("name", ["T", "len"])
def test_unbound_local_shadows_type_parameter(tmp_path: Path, name: str) -> None:
    source = "\n".join(f"""def {function}[{name}](flag):
    if flag:
        {name} = 7
    if flag:
        a = {name} + 1
    else:
        a = 5
    b = a * 2
    c = b - 3
    return c
""" for function in ("first", "second"))
    _preserved(tmp_path, source, "print(first(False), second(False), first(True), second(True))")


@pytest.mark.parametrize("control", ["break", "continue"])
def test_match_loop_control(tmp_path: Path, control: str) -> None:
    source = "\n".join(f"""def {name}(items):
    result = []
    for n in items:
        match n:
            case {value}:
                total = n * 3
                total = total + 7
                result.append(total)
                {control}
    return result
""" for name, value in (("f", 0), ("g", 1)))
    _preserved(tmp_path, source, "print(f([0, 1, 0]), g([0, 1, 1]))")


def test_except_star_cleanup_and_subgroups(tmp_path: Path) -> None:
    from tests.test_candidate_recovery import EXCEPT_STAR

    _preserved(
        tmp_path,
        EXCEPT_STAR,
        """print(f(2), g(3))
for function in (f, g):
    try:
        function(4, mixed=True)
    except ExceptionGroup as remaining:
        print('unhandled', [type(error).__name__ for error in remaining.exceptions])
""",
    )


@pytest.mark.parametrize("private", [False, True])
def test_captured_method_receiver(tmp_path: Path, private: bool) -> None:
    from tests.test_method_helper_hosting import CAPTURED_RECEIVER

    source = (
        CAPTURED_RECEIVER.replace("alpha", "__alpha").replace("beta", "__beta")
        if private
        else CAPTURED_RECEIVER
    )
    _preserved(tmp_path, source, "print(C().first(1, 2), C().second(1, 2), events)")


def test_super_keeps_implicit_receiver(tmp_path: Path) -> None:
    from tests.test_argument_ownership import SUPER_CAPTURED_RECEIVER

    _preserved(tmp_path, SUPER_CAPTURED_RECEIVER, "print(C().first(1), C().second(2), events)")


@pytest.mark.parametrize("method_kind", ["instance", "staticmethod", "classmethod"])
def test_generic_method_dispatch(tmp_path: Path, method_kind: MethodKind) -> None:
    from tests.test_generic_methods import _ordinary_source

    _preserved(
        tmp_path,
        _ordinary_source(method_kind),
        """class Derived(Calculator):
    pass
instance = Derived()
print(instance.integers(2, 3), instance.strings('a', 'b'), instance.calls, Derived.calls, Calculator.calls)
""",
    )


@pytest.mark.parametrize(
    "kind, target",
    [
        ("lambda", "o"),
        ("lambda", "r"),
        ("generator", "o"),
        ("generator", "r"),
        ("outer-generator", "o"),
        ("lambda-default", "o"),
        ("lambda-default", "r"),
        ("lambda-default-argument", "o"),
        ("lambda-default-argument", "r"),
    ],
)
def test_captured_comprehension_cell(tmp_path: Path, kind: str, target: str) -> None:
    from tests.test_comprehension_cell_capture_cli import _source, _RUN

    _preserved(tmp_path, _source(kind, target), _RUN)


def test_clustered_live_names(tmp_path: Path) -> None:
    from tests.test_forwarder_chains import _chain

    _preserved(
        tmp_path,
        _chain(12),
        """from types import SimpleNamespace
order = SimpleNamespace(id=7, items=[SimpleNamespace(price=10.0, in_stock=True), SimpleNamespace(price=5.0, in_stock=False)])
print([globals()['f' + str(index)]() for index in range(12)])
""",
    )


@pytest.mark.parametrize("generated", [False, True])
def test_function_specialization_keeps_effects(tmp_path: Path, generated: bool) -> None:
    from tests.test_forwarder_chains import (
        GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT,
        _collection_trace,
    )

    source = GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT
    if not generated:
        source = source.replace("__extracted_func_0", "square_shifted")
    path = tmp_path / "subject.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(strict=False, skip_trivial_helpers=False)
    output, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied > 0, engine.declined_pairs
    assert _collection_trace(output) == _collection_trace(source)


def _project_preserved(root: Path, files: dict[str, str], driver: str) -> None:
    from tests.test_parameterize_builtins import _write, _run

    _write(root, {**files, "driver.py": driver})
    before = _run(root, "driver.py")
    engine = UnificationRefactorEngine(
        strict=False,
        min_lines=2,
        max_parameters=8,
        skip_trivial_helpers=False,
        cross_module_helpers=True,
    )
    results, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    assert sum(count for count, _ in results.values()) > 0, engine.declined_pairs
    assert _run(root, "driver.py") == before


def test_private_class_helpers_and_super(tmp_path: Path) -> None:
    from tests.test_class_private_helpers import SAME_CLASS, SAME_CLASS_DRIVER

    _project_preserved(
        tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": SAME_CLASS}, SAME_CLASS_DRIVER
    )


def test_builtin_module_patch(tmp_path: Path) -> None:
    from tests.test_builtins_across_modules import SAME_MODULE, SAME_MODULE_CHECK

    _project_preserved(tmp_path, {"m.py": SAME_MODULE}, SAME_MODULE_CHECK)


def test_patching_one_duplicate_keeps_the_other_independent(tmp_path: Path) -> None:
    from tests.test_functions_keep_their_own_bodies import SAME_BODIES, PATCHING_DRIVER

    _project_preserved(tmp_path, {"c.py": SAME_BODIES}, PATCHING_DRIVER)


@pytest.mark.parametrize("name", ["named", "__extracted_func_0"])
def test_existing_function_name_does_not_change_other_callers(tmp_path: Path, name: str) -> None:
    source = f"""events = []
def {name}(n):
    value = n + 3
    result = value * 2
    events.append(('same', result))
    return result
def second(n):
    value = n + 3
    result = value * 2
    events.append(('same', result))
    print('after block', result)
    return result
"""
    _preserved(
        tmp_path,
        source,
        f"""first = {name}
print(first(1), second(2), events)
{name} = lambda n: 'patched'
print(second(3), events)
""",
    )


@pytest.mark.parametrize("safe", [False, True])
def test_helpers_only_import_shipped_packages(tmp_path: Path, safe: bool) -> None:
    from tests.test_host_ships_with_borrower import FLAT_PYPROJECT, _function, _import_shipped

    files = {
        "pyproject.toml": FLAT_PYPROJECT,
        "zeta/__init__.py": "",
        "zeta/a.py": _function("fa", "a", 1, safe=safe),
        "tests/__init__.py": "",
        "tests/test_b.py": "import zeta.a\n\n" + _function("test_fb", "bb", 2, safe=safe),
    }
    _project_preserved(
        tmp_path,
        files,
        "from zeta import a; from tests import test_b; print(a.fa([1, -2], 2), test_b.test_fb([1, -2], 3))",
    )
    assert _import_shipped(tmp_path, ["zeta"], ["zeta", "zeta.a"], tmp_path).split() == [
        "zeta",
        "ok",
        "zeta.a",
        "ok",
    ]


@pytest.mark.parametrize("safe", [False, True])
def test_helper_imports_close_no_cycle(tmp_path: Path, safe: bool) -> None:
    from tests.test_import_cycles_by_the_program import _write, _imports

    root = _write(tmp_path / "project", safe=safe)
    before = {name: _imports(root, name) for name in ("alpha.a_host", "alpha.b_borrower")}
    engine = UnificationRefactorEngine(
        strict=False, skip_trivial_helpers=False, cross_module_helpers=True
    )
    results, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    assert sum(count for count, _ in results.values()) > 0, engine.declined_pairs
    for name, expected in before.items():
        assert expected[0] == 0
        assert _imports(root, name) == expected


def test_borrower_patch_precedes_host_import(tmp_path: Path) -> None:
    from tests.test_import_position import _SUMMARY

    _project_preserved(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="shop"\nversion="0"\n',
            "shop/__init__.py": "",
            "shop/a.py": "from time import sleep\n\ndef use_sleep():\n    sleep(0)\n\n"
            + _SUMMARY.format(n="a", N="A"),
            "shop/b.py": "import time\ntime.sleep = lambda s: print('patched sleep', s)\nimport shop.a\n\n"
            + _SUMMARY.format(n="b", N="B"),
        },
        "import shop.b, shop.a; shop.a.use_sleep(); print(shop.b.summarize_b(['ab'], 2))",
    )


def test_cross_module_helper_reuse_preserves_values(tmp_path: Path) -> None:
    from tests.test_generated_helper_reuse import _package, _runtime

    root = tmp_path / "project"
    _package(root, 3)
    before = _runtime(root, 3)
    engine = UnificationRefactorEngine(strict=False, min_lines=3, cross_module_helpers=True)
    results, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    assert sum(count for count, _ in results.values()) > 0, engine.declined_pairs
    assert _runtime(root, 3) == before


def test_directory_run_imports_usable_crossfile_helper(tmp_path: Path) -> None:
    from tests.test_crossfile_integration import TestExampleThreeCrossFile
    from tests.test_parameterize_builtins import _run

    root = tmp_path / "example3"
    TestExampleThreeCrossFile._premium_imports_regular(root)
    (root / "drive.py").write_text("""import example3_file1 as regular
import example3_file2 as premium
for customer in ({}, {'years_member': 9}, {'total_purchases': 2000}, {'years_member': 9, 'total_purchases': 2000}):
    print(regular.calculate_discount_for_regular_customer(200, customer), premium.calculate_discount_for_premium_customer(200, customer))
""")
    before = _run(root, "drive.py")
    engine = UnificationRefactorEngine(
        strict=False, skip_trivial_helpers=False, cross_module_helpers=True
    )
    result, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    assert sum(count for count, _ in result.values()) > 0, engine.declined_pairs
    assert _run(root, "drive.py") == before


def test_inventory_records_executable_changes(tmp_path: Path) -> None:
    from towel.cli import _write_change_sidecar, _find_extracted_helpers
    from towel.cli import helper_inventory

    # The inventory describes generated helpers; decorated sites exclude redirects.
    source = (
        "@staticmethod\ndef f(a):\n"
        "    x = a + 1\n    y = x * 2\n    z = y + compute(a)\n    return z\n\n"
        "@staticmethod\ndef g(b):\n"
        "    x = b + 1\n    y = x * 2\n    z = y + compute(b)\n    return z\n\n"
        "def compute(v):\n    return v\n"
    )
    path = tmp_path / "program.py"
    driver = "\nprint(f(2), g(3))\n"
    path.write_text(source + driver)
    before = _observe(path)
    path.write_text(source)
    engine = UnificationRefactorEngine(strict=False, skip_trivial_helpers=False, min_lines=3)
    engine.refactor_directory_to_fixed_point(str(tmp_path), str(tmp_path), progress="none")
    assert engine.change_log
    _write_change_sidecar(engine, str(tmp_path))
    inventory = helper_inventory(tmp_path, _find_extracted_helpers(tmp_path, None, None))
    assert any(entry["changes"] for entry in inventory["helpers"])
    path.write_text(path.read_text() + driver)
    assert before[0] == 0 and _observe(path) == before


def test_original_sql_generator_preserves_rendering(tmp_path: Path) -> None:
    from tests.test_forwarder_chains import SQL_GENERATOR, _rendered_sql

    path = tmp_path / "generator.py"
    path.write_text(SQL_GENERATOR)
    engine = UnificationRefactorEngine(strict=False, min_lines=3, skip_trivial_helpers=False)
    final, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied > 0, engine.declined_pairs
    assert _rendered_sql(final) == _rendered_sql(SQL_GENERATOR)


def test_original_partial_collection_preserves_values(tmp_path: Path) -> None:
    from tests.test_forwarder_chains import OWNED_GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT

    _preserved(
        tmp_path,
        OWNED_GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT,
        "print(collect([1, 3], 2), collect([-2, 5], 7))",
    )


def test_import_named_like_owner_keeps_constructor_effects(tmp_path: Path) -> None:
    from tests.test_argument_ownership import SOURCE

    prefix = SOURCE[: SOURCE.index("def f(a):")].replace(
        "def __init__(self,name): self.name=name",
        'def __init__(self,name): self.name=name; events.append("born-"+name)',
    )
    source = prefix + """def f(a):
    inside = R('inside')
    import math as _towel_owner
    local = R('local')
    return 11

def g(a):
    inside = R('inside')
    import math as _towel_owner
    local = R('local')
    return 11
"""
    _preserved(
        tmp_path,
        source,
        "print(f(R('a')), g(R('b')))\nprint([event for event in events if event.startswith('born-')])",
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax requires Python 3.12")
def test_generic_header_with_resource_parameter(tmp_path: Path) -> None:
    from tests.test_argument_ownership import SOURCE

    source = SOURCE[: SOURCE.index("def f(a):")] + """def f[T](a: T):
    local = R('local-f')
    return 11

def g[U](a: U):
    local = R('local-g')
    return 11
"""
    _preserved(
        tmp_path,
        source,
        "print(f(R('a')), g(R('b')), f.__type_params__[0].__name__, g.__type_params__[0].__name__)",
    )
