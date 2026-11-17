# Copyright 2025-2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
"""Ordinary behavior behind the strict suite's ownership-sensitive expectations.

These companions retain the original fixtures where possible. Related strict
assertions share a behavioral case: the audit table records that relationship.
Every engine explicitly selects relaxed mode, including with --towel-strict.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests import test_module_names_stay_free as module_names
from tests import test_module_header_bindings as header_bindings
from tests import test_optional_converter_narrowing as optional_returns
from tests import test_thunk_ordering_cli as thunk_ordering
from tests import test_thunk_result_lifetimes as thunk_factories
from tests import test_no_builtin_arguments as builtin_arguments
from tests import test_parameterize_builtins as parameterized_builtins
from tests import test_receiver_dependency as receiver
from tests import test_return_alignment as alignment
from tests import test_returned_variables_not_orphaned as returned
from tests import test_semantic_guard_traces as semantic_traces
from tests import test_unparsed_program as unparsed
from towel.type_inference import CheckFailure, MypyInferrer, PyrightOracle
from towel.cli import LACKING_MODULE_REMEDY
from towel.unification import pipeline
from towel.unification.argument_ownership import _supported_runtime
from tests.test_helpers import example_paths
from tests.test_declined_run_reports import _similar_functions
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.relaxed_mode


@dataclass(frozen=True)
class Program:
    name: str
    source: str
    driver: str
    parameterize_builtins: bool = False


def _observe(source: str, driver: str) -> str:
    ran = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source + "\n" + driver],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stderr
    assert not ran.stderr
    return ran.stdout


def _transform(
    path: Path,
    source: str,
    *,
    typed: bool = False,
    parameterize_builtins: bool = False,
    pyright: bool = False,
) -> str:
    path.write_text(source)
    oracle = (PyrightOracle() if pyright else MypyInferrer()) if typed else None
    try:
        engine = UnificationRefactorEngine(
            strict=False,
            min_lines=2,
            max_parameters=8,
            skip_trivial_helpers=False,
            reuse_existing_functions=False,
            parameterize_builtins=parameterize_builtins,
            type_oracle=oracle,
        )
        result, count, _ = engine.refactor_to_fixed_point(
            str(path), output_path=str(path.with_name("after.py")), progress="none"
        )
        assert count > 0, engine.declined_pairs
        assert path.read_text() == source
        return result
    finally:
        if oracle is not None:
            oracle.close()


_MODULE_DRIVER = """
from types import SimpleNamespace
text = ' [{"name":"a"}] '
print(first(text, True), second(text, True))
helper = lambda text: 'patched'
json = SimpleNamespace(loads=lambda text: [{'name': 'rebound'}])
print(first(text, True), second(text, True))
"""
_CALLBACKS = """
value = 1
def update():
    global value
    value += 1
def first():
    before = value
    update()
    after = value
    return before, after
def second():
    value_seen = value
    before = value
    update()
    after = value
    return before, after, value_seen
"""
_NESTED = """
def outer1(data, threshold):
    def make_validator(limit):
        return lambda x: x > limit
    def make_transformer(factor):
        return lambda x: x * factor + threshold
    validator = make_validator(5)
    transformer = make_transformer(2)
    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))
    return transformed

def outer2(data, threshold):
    def make_validator(limit):
        return lambda x: x > limit
    def make_transformer(factor):
        return lambda x: x * factor + threshold
    validator = make_validator(5)
    transformer = make_transformer(2)
    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))
    return transformed
"""
_METHODS = """
class Example:
    offset = 3
    def alpha(self, value):
        tmp = value + self.offset
        return tmp * 2
    def beta(self, value):
        tmp = value + self.offset
        return tmp * 2
"""
_NONLOCAL = """
def outer():
    count = 0
    def first(value):
        nonlocal count
        count += 1
        total = value + 1
        doubled = total * 2
        print(doubled)
        return doubled
    def second(value):
        nonlocal count
        count += 2
        total = value + 1
        doubled = total * 2
        print(doubled)
        return doubled
    left, right = first(3), second(5)
    return left, right, count
"""
_PRIVATE = """
class First:
    __secret = 7
    def calculate(self, value):
        print(self.__secret)
        total = value + 1
        doubled = total * 2
        print(doubled)
        return doubled
class Second:
    __secret = 11
    def calculate(self, value):
        print(self.__secret)
        total = value + 1
        doubled = total * 2
        print(doubled)
        return doubled
"""
_LOOPS = """
def alpha(items, log):
    for i in items:
        log.append(i * 2)
    log.append(len(items))
def beta(items, log):
    for i in items:
        log.append(i * 2)
    log.append(len(items))
"""
_NORMALIZATION = """
def norm(items):
    total = sum(items)
    scaled = [i / total for i in items]
    return scaled
def report(data, label):
    print(label)
    total = sum(data)
    scaled = [i / total for i in data]
    return scaled
"""
_ASYNC = """
async def alpha(value):
    tmp = value + 1
    total = tmp * 2
    return total
async def gamma(value):
    tmp = value + 1
    total = tmp * 2
    return total
"""
_CONDITIONAL = """
FLAG = True
def f():
    if FLAG:
        doubled = 20
        answer = doubled * 2
    print('after')
    return answer + 3
def g():
    if FLAG:
        doubled = 45
        answer = doubled * 2
    print('after')
    return answer + 4
"""
_CONDITIONAL_DRIVER = """
for FLAG in (True, False):
    for function in (f, g):
        try:
            print(function())
        except UnboundLocalError:
            print('unbound')
"""

PROGRAMS = (
    Program("module-names", module_names.TWO_SITES, _MODULE_DRIVER),
    Program(
        "local-builtins",
        builtin_arguments._function(builtin_arguments.LOCAL_30, "first")
        + "\n"
        + builtin_arguments._function(builtin_arguments.LOCAL_40, "second"),
        "print(first([1, 2], 'a'), second([3], 'b'))",
    ),
    Program(
        "mixed-builtins",
        parameterized_builtins.MIXED,
        "print(first([1, 2], 'a'), second([3], 'b'))",
        True,
    ),
    Program(
        "clustered-builtins",
        parameterized_builtins.CLUSTERED,
        "print(first([1, 2], 'a'), second([3], 'b'), third([], 'c'))",
        True,
    ),
    Program("module-callbacks", _CALLBACKS, "print(first(), second())"),
    Program("nested-factories", _NESTED, "print(outer1([1,5,6,9],3), outer2([1,5,6,9],7))"),
    Program("instance-methods", _METHODS, "e=Example(); print(e.alpha(2),e.beta(4))"),
    Program(
        "class-methods",
        _METHODS.replace("    def ", "    @classmethod\n    def ").replace("self", "cls"),
        "print(Example.alpha(2),Example.beta(4))",
    ),
    Program(
        "static-methods",
        _METHODS.replace("    def ", "    @staticmethod\n    def ")
        .replace("self, ", "")
        .replace("self.offset", "3"),
        "print(Example.alpha(2),Example.beta(4))",
    ),
    Program("receiver", receiver.USES_RECEIVER, "print(main())"),
    Program("nonlocal", _NONLOCAL, "print(outer())"),
    Program("private-attributes", _PRIVATE, "print(First().calculate(3),Second().calculate(5))"),
    Program(
        "return-alignment",
        alignment.SOURCE,
        "for items in ([],[1,2,3]):\n    print(f(items),g(items))",
    ),
    Program("conditional-binding", _CONDITIONAL, _CONDITIONAL_DRIVER),
    Program(
        "implicit-none",
        _LOOPS,
        "for items in ([],[1,3]):\n    log=[]; print(alpha(items,log),beta(items,log),log)",
    ),
    Program("normalization", _NORMALIZATION, "print(norm([1,3]),report([2,2],'report'))"),
    Program("async", _ASYNC, "import asyncio; print(asyncio.run(alpha(3)),asyncio.run(gamma(5)))"),
    Program(
        "partial-returns",
        semantic_traces.PARTIAL_RETURN
        + semantic_traces.PARTIAL_RETURN.replace("partial", "partial_twin"),
        "for n in (-1,0,11):\n    print(partial(n),partial_twin(n))",
    ),
    Program("unparsed-instrumenters", unparsed.KERNELS, "print(first(3),second(5))"),
)


@pytest.mark.parametrize("case", PROGRAMS, ids=lambda case: case.name)
def test_relaxed_program_behavior(tmp_path: Path, case: Program) -> None:
    source = textwrap.dedent(case.source).lstrip()
    before = _observe(source, case.driver)
    result = _transform(tmp_path / "m.py", source, parameterize_builtins=case.parameterize_builtins)
    assert _observe(result, case.driver) == before


@pytest.mark.parametrize(
    "source", [returned.README_EXAMPLE, returned.OWNED_PARTIAL], ids=["readme", "partial"]
)
def test_relaxed_returned_variables(tmp_path: Path, source: str) -> None:
    before = returned._run(source)
    result = _transform(tmp_path / "m.py", source)
    assert returned._run(result) == before == ["Order 7: $10.8", "Quote 7: $10.8"]


@pytest.mark.parametrize("header", header_bindings.HEADERS)
@pytest.mark.parametrize("position", ["before", "between", "after"])
@pytest.mark.parametrize("flag", [False, True])
def test_relaxed_header_builtin_mappings(
    tmp_path: Path, header: str, position: str, flag: bool
) -> None:
    from tests.test_definition_time_builtins import _source

    source = _source(**{position: header})
    before = _observe(source, "")
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        strict=False, min_lines=3, skip_trivial_helpers=False, parameterize_builtins=flag
    )
    result, _, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert _observe(result, "") == before


@pytest.mark.parametrize(
    "source",
    [semantic_traces.LATER_BOUND_AFTER_IN_BOTH, semantic_traces.LATER_BOUND_AFTER_IN_SECOND_ONLY],
)
def test_relaxed_late_binding(tmp_path: Path, source: str) -> None:
    driver = """
for function in (first, second):
    try:
        print(function([1, 2]))
    except UnboundLocalError:
        print('unbound')
"""
    before = _observe(source, driver)
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(strict=False, min_lines=2, skip_trivial_helpers=False)
    result, _, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert _observe(result, driver) == before
    assert any(reason.startswith("incomplete_lifetime") for reason in engine.declined_pairs)


@pytest.mark.parametrize("opaque", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_relaxed_suppressing_context(tmp_path: Path, opaque: bool, typed: bool) -> None:
    from tests.test_cli_integration import invoke

    source = (
        Path(__file__).parent / "hostile_cases" / "r1792_aliased_suppress_conditional_read.py"
    ).read_text()
    if opaque:
        source = source.replace(
            "from contextlib import suppress as absorb",
            "class absorb:\n"
            "    def __init__(self, exception: type[Exception]) -> None:\n        pass\n"
            "    def __enter__(self) -> None:\n        pass\n"
            "    def __exit__(self, *exc_info: object) -> bool:\n        return True\n",
        )
    driver = """
for function in (f, g):
    for flag in (False, True):
        try:
            print(function(flag))
        except UnboundLocalError as error:
            print(type(error).__name__, str(error))
"""
    before = _observe(source, driver)
    path, output = tmp_path / "m.py", tmp_path / "out.py"
    path.write_text(source)
    arguments = [
        "dry",
        str(path),
        str(output),
        "--no-strict",
        "--no-interactive",
        "--progress",
        "none",
    ]
    if not typed:
        arguments.append("--no-types")
    result = invoke(arguments)
    assert result.status == 0, result.stderr
    transformed = output.read_text()
    # Every candidate reads ``first``/``second``, which the suppressed
    # division may leave unbound. Since byvalue's second round relaxed mode
    # passes a name by value only where the call site is sure to resolve it,
    # so each candidate would need a lambda over a possibly unbound local and
    # is declined, typed or not. (Until then the untyped run passed ``first``
    # by value into a helper of the arithmetic suffix, and the typed run
    # declined that suffix as unprofitable_helper.)
    assert "thunk_of_possibly_unbound_local" in result.stdout
    assert "with similar code and extracted none." in result.stdout
    assert ast.dump(ast.parse(transformed)) == ast.dump(ast.parse(source))
    assert _observe(transformed, driver) == before


def _run_project(root: Path, driver: str) -> str:
    prelude = f"import sys; sys.path.insert(0, {str(root)!r})\n"
    return _observe(prelude, driver)


def _transform_project(root: Path, *, typed: bool = False) -> int:
    oracle = MypyInferrer() if typed else None
    try:
        engine = UnificationRefactorEngine(
            strict=False,
            min_lines=2,
            max_parameters=8,
            skip_trivial_helpers=False,
            parameterize_builtins=True,
            cross_module_helpers=True,
            type_oracle=oracle,
        )
        results, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
        return sum(count for count, _ in results.values())
    finally:
        if oracle is not None:
            oracle.close()


@pytest.mark.parametrize("local", [False, True])
def test_relaxed_cross_module_builtin_lookup(tmp_path: Path, local: bool) -> None:
    if local:
        files = builtin_arguments._package(
            "export_size(rows, name, len=lambda value: 40)",
            "report_size(rows, name, len=lambda value: 30)",
        )
    else:
        files = parameterized_builtins._package()
    builtin_arguments._write(tmp_path, files)
    root = tmp_path if local else tmp_path / "proj"
    driver = parameterized_builtins._driver(
        ["pkg.exports", "pkg.reports"],
        [("pkg.exports", "export_size"), ("pkg.reports", "report_size")],
    )
    before = _run_project(root, driver)
    assert _transform_project(root / "pkg") > 0
    assert _run_project(root, driver) == before


def test_relaxed_cross_module_names(tmp_path: Path) -> None:
    body = """
    total = sum(items)
    total = scale(total)
    total += 1
    print(TAG, total)
    return total
"""
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def scale(value):\n    return value * 2\n\ndef first(items):\n"
        + body.replace("TAG", "'first'"),
        "pkg/b.py": "import pkg.a\ndef scale(value):\n    return value * 3\n\ndef second(items):\n"
        + body.replace("TAG", "'second'"),
    }
    builtin_arguments._write(tmp_path, files)
    driver = (
        "from pkg import a,b; print(a.first([1,2]),b.second([1,2])); "
        "a.scale=lambda x:x*10; print(a.first([1,2]),b.second([1,2]))"
    )
    before = _run_project(tmp_path, driver)
    assert _transform_project(tmp_path / "pkg") > 0
    assert _run_project(tmp_path, driver) == before


@pytest.mark.parametrize("memo", [False, True])
@pytest.mark.parametrize("cross_module", [False, True])
def test_relaxed_occurrence_vectors(tmp_path: Path, memo: bool, cross_module: bool) -> None:
    from contextlib import nullcontext
    from tests import test_occurrence_constant_cli as occurrences
    from towel.unification.bounded_cache import memoization_disabled

    case = occurrences._Case("pooled_permutation", (("1000000", "2"), ("2", "1000000")), "1000000")
    sources, driver = occurrences._sources(case, cross_module)
    # These two finalizers only log lifetime observations. Remove exactly those
    # observations, leaving constructor, operator, exception and identity events
    # in their original order. No output token is sorted or discarded otherwise.
    driver = driver.replace("EVENTS.append(('drop-result', self.serial))", "pass")
    driver = driver.replace("EVENTS.append(('drop-input', int(self)))", "pass")
    root = occurrences._project(tmp_path / "project", sources)
    before = _run_project(root, driver)
    with nullcontext() if memo else memoization_disabled():
        assert _transform_project(root, typed=True) > 0
    expected = _moved_literal_identity(before) if cross_module else before
    assert _run_project(root, driver) == expected


def _moved_literal_identity(output: str) -> str:
    """The one change "Relaxed mode moves equal literals into a shared helper" permits here.

    With the helper in a.py, f1's ``x * 1000000`` multiplies by a.py's
    constant, which is not b.py's ``COMMON`` (docs/design/DECISIONS.md: a
    program comparing such literals with ``is`` needs ``--strict``). Only
    that identity flag of f1's traces changes; every other event, its order
    and every result stay as they were.
    """
    same, moved = (f"('input-mul', ('int', '1000000'), {flag})" for flag in ("True", "False"))
    lines, owner = [], ""
    for line in output.splitlines(keepends=True):
        if not line.startswith("["):
            owner = line.split(" ", 1)[0]
        lines.append(line.replace(same, moved) if owner == "f1" and line.startswith("[") else line)
    changed = "".join(lines)
    assert changed != output, "f1 multiplies by the shared literal"
    return changed


@pytest.mark.parametrize("expression", ["OFFSET", "supply()"])
def test_relaxed_mixed_expressions(tmp_path: Path, expression: str) -> None:
    prelude = """
OFFSET = 2
EVENTS = []
FAIL = False
def supply():
    EVENTS.append('supply')
    if FAIL:
        raise ValueError('supplier failed')
    return OFFSET
"""
    source = prelude + "\n".join(
        f"def f{index}(x):\n    first=x+{offset}\n    scale=x*2\n"
        "    total=first+scale\n    checked=total+11\n    return checked\n"
        for index, offset in enumerate(("1", expression))
    )
    driver = """
class Value(int):
    def __add__(self, other):
        EVENTS.append(('add', other))
        if MODE == 'add-error':
            raise ValueError('operand failed')
        return int(self)+other
    def __mul__(self, other):
        EVENTS.append(('mul', other))
        if MODE == 'mul-error':
            raise ValueError('multiplication failed')
        return int(self)*other
for FAIL in (False, True):
    for MODE in ('ok','add-error','mul-error'):
        for function in (f0,f1):
            EVENTS.clear()
            try:
                print(function(Value(3)))
            except ValueError as error:
                print(str(error))
            print(EVENTS)
"""
    before = _observe(source, driver)
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(strict=False, min_lines=2, skip_trivial_helpers=False)
    result, count, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    if expression == "supply()":
        assert count > 0
    assert _observe(result, driver) == before


_TYPED_BOX = """
class Box:
    def __init__(self, value: int, name: str) -> None:
        self.value = value
        self.name = name

def first(box: Box) -> str:
    scaled = box.value * 2
    label = box.name.upper()
    return label + str(scaled)

def second(box: Box) -> str:
    scaled = box.value * 3
    label = box.name.upper()
    return label + str(scaled)
"""
_TYPED_RETURN = """
def first(value: int) -> int:
    total = value * 2
    text = str(total)
    return len(text.strip())
def second(value: int) -> OTHER:
    total = value * 2
    text = str(total)
    return len(text.strip())
"""
_TYPED_UNION = """
class Box:
    def __init__(self) -> None:
        self.count = 1
        self.ratio = 2.5
def first(box: Box) -> str:
    scaled = box.count * 2
    label = str(scaled).upper()
    return label.strip()
def second(box: Box) -> str:
    scaled = box.ratio * 2
    label = str(scaled).upper()
    return label.strip()
"""
_TYPED_ANY = """
import json
def first() -> int:
    payload = json.loads('{"a":1}')
    keys = sorted(payload)
    print(keys)
    return len(keys)
def second() -> int:
    payload = json.loads('{"a":1}')
    keys = sorted(payload)
    print(keys)
    return len(keys) * 2
"""
_TYPED_CLASS = """
class Base: ...
class Box(Base): ...
def make(flag: bool) -> Box:
    box = Box()
    print(flag, box)
    return box
def build(flag: bool) -> Base:
    box = Box()
    print(flag, box)
    return box
"""
_TYPED_PROGRAMS = (
    Program("box", _TYPED_BOX, "print(first(Box(3,'a')),second(Box(5,'b')))"),
    Program("union", _TYPED_UNION, "print(first(Box()),second(Box()))"),
    Program("any", _TYPED_ANY, "print(first(),second())"),
    Program(
        "class",
        _TYPED_CLASS,
        "Box.__repr__=lambda self:'Box'; print(type(make(True)).__name__,type(build(False)).__name__)",
    ),
    *(
        Program(
            "return-" + annotation,
            _TYPED_RETURN.replace("OTHER", annotation),
            "for value in (0,3,20):\n    print(first(value),second(value))",
        )
        for annotation in ("int", "object", "int | None")
    ),
)


@pytest.mark.parametrize("case", _TYPED_PROGRAMS, ids=lambda case: case.name)
@pytest.mark.parametrize("pyright", [False, True])
def test_relaxed_typed_behavior(tmp_path: Path, case: Program, pyright: bool) -> None:
    source = textwrap.dedent(case.source).lstrip()
    before = _observe(source, case.driver)
    result = _transform(tmp_path / "m.py", source, typed=True, pyright=pyright)
    assert _observe(result, case.driver) == before


@pytest.mark.parametrize(
    "fixture", optional_returns.OPTIONAL_RETURN_FIXTURES, ids=lambda fixture: fixture.name
)
def test_relaxed_optional_returns(tmp_path: Path, fixture: optional_returns.Fixture) -> None:
    from tests import test_optional_converter_narrowing as optional

    assert isinstance(fixture, optional.Fixture)
    path = tmp_path / "subject.py"
    source = fixture.source
    result = _transform(path, source, typed=True)
    observer = optional.TASK_OBSERVER
    # Only finalizer log entries are relaxed. Attribute reads, conversions,
    # results, and raised exceptions stay in order and are compared exactly.
    observer = observer.replace('EVENTS.append(("drop-number", self.serial))', "pass")
    observer = observer.replace('EVENTS.append(("drop-task",))', "pass")
    script = tmp_path / "observe.py"
    script.write_text(observer)
    before = optional._observe(path, script, fixture)
    after = optional._observe(path.with_name("after.py"), script, fixture)
    assert before[0] == 0 and len(before[1].splitlines()) == 32
    assert result != source
    assert after == before


def test_relaxed_comments(tmp_path: Path) -> None:
    from tests import test_moved_comments as comments

    source = textwrap.dedent(comments._EXPLAINED).lstrip()
    driver = "for value in (0, 1, 3, 5):\n    print(first(), second())"
    before = _observe(source, driver)
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, driver) == before
    assert "# large values count double" in result
    assert "# the bonus for a large value" in result


@pytest.mark.parametrize("directive", ["# fmt: skip", "# fmt: off"])
def test_relaxed_layout_behavior(tmp_path: Path, directive: str) -> None:
    source = "# fmt: off\n" if directive == "# fmt: off" else ""
    source += "\n".join(
        f"def g{index}(rows):\n    total=0\n    for row in rows:\n"
        "        total += row  *  2  # fmt: skip\n    print(total)\n"
        f"    return total + {index}\n"
        for index in (1, 2)
    )
    driver = "for rows in ([],[1,3]):\n    print(g1(rows),g2(rows))"
    before = _observe(source, driver)
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, driver) == before
    assert "total += row  *  2  # fmt: skip" in result


def test_relaxed_shared_line_directive(tmp_path: Path) -> None:
    source = """
def f1(n):
    a = n + 1; b = a * 2  # noqa: E702
    c = a + b
    print('f1', a, b, c)
    return c
def f2(n):
    a: int = n + 2; b = a * 2  # noqa: E702
    c = a + b
    print('f2', a, b, c)
    return c
"""
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(strict=False, min_lines=3, parameterize_constants=True)
    proposals = engine.analyze_file(str(path))
    assert "directive_on_shared_line" in engine.declined_pairs
    driver = "print(f1(2),f2(4))"
    before = _observe(source, driver)
    for proposal in proposals:
        assert _observe(engine.apply_refactoring(str(path), proposal), driver) == before


def test_relaxed_pipeline_and_statistics(tmp_path: Path) -> None:
    from tests import test_pipeline_phases as phases
    from tests import test_run_statistics as statistics

    files = example_paths(["example1_simple.py"])
    modules = pipeline.analyze_scopes(pipeline.parse_modules(files))
    functions = pipeline.collect_functions(modules)
    classes = pipeline.collect_classes(modules)
    engine = UnificationRefactorEngine(strict=False, skip_trivial_helpers=False)
    pairs = pipeline.pair_blocks(engine, functions, progress="none")
    proposals = pipeline.unify_blocks(engine, pairs, functions, classes, progress="none")
    filtered = pipeline.filter_overlaps(proposals)
    assert filtered
    before = phases.observed_process_output(modules[0].source)
    for proposal in filtered:
        result = engine.apply_refactoring(files[0], proposal)
        assert phases.observed_process_output(result) == before
    root = tmp_path / "project"
    path = _similar_functions(root, 6)
    source = path.read_text()
    driver = "print([globals()['f'+str(i)]([1,-2],3) for i in range(6)])"
    before = _observe(source, driver)
    engine = UnificationRefactorEngine(strict=False, skip_trivial_helpers=False, min_lines=2)
    engine.refactor_directory_to_fixed_point(str(root), str(tmp_path / "out"), progress="none")
    report = engine.run_report
    assert report.candidate_pairs == (
        sum(report.declined_pairs.values())
        + report.duplicate_pairs
        + report.overlapping_proposals
        + report.emitted_proposals
    )
    fresh = UnificationRefactorEngine(strict=False, skip_trivial_helpers=False, min_lines=2)
    tally, _, emitted = statistics._analyzed(fresh, tmp_path / "out/m.py")
    assert report.candidate_pairs == tally.candidate_pairs
    assert report.emitted_proposals == emitted
    assert _observe((tmp_path / "out/m.py").read_text(), driver) == before


def test_relaxed_mypy_plugin(tmp_path: Path) -> None:
    from tests import test_mypy_plugins as plugins

    root = plugins._project(tmp_path / "project")
    path = root / "src" / "alpha" / "b.py"
    path.write_text("import alpha.a\n" + path.read_text())
    driver = "from alpha import a,b; a.fa([1,2]); b.fb([1,2])"
    before = _run_project(root / "src", driver)
    assert plugins._fresh_mypy(root) == []
    assert _transform_project(root / "src" / "alpha", typed=True) > 0
    assert _run_project(root / "src", driver) == before
    assert plugins._fresh_mypy(root) == []


def test_relaxed_typed_builtin_parameters(tmp_path: Path) -> None:
    files = {
        "pyproject.toml": '[project]\nname="pkg"\nversion="0"\n[tool.mypy]\nstrict=true\n',
        "pkg/__init__.py": "",
        "pkg/exports.py": parameterized_builtins.TYPED_EXPORTS,
        "pkg/reports.py": parameterized_builtins.TYPED_REPORTS,
        "tests/test_exports.py": parameterized_builtins.TYPED_TEST,
    }
    builtin_arguments._write(tmp_path, files)
    driver = "from pkg import exports,reports; print(exports.export_size([1,2],'ab'),reports.report_size([1,2],'ab'))"
    before = _run_project(tmp_path, driver)
    assert _transform_project(tmp_path / "pkg", typed=True) > 0
    assert _run_project(tmp_path, driver) == before


@pytest.mark.parametrize("safe", [False, True])
def test_relaxed_vendored_import_effects(tmp_path: Path, safe: bool) -> None:
    from tests import test_program_imports as imports

    root = imports._vendoring(tmp_path / "project", safe=safe)
    driver = """
import pkg.a_host as host
import pkg.b_borrower as borrower
for function in (host.fa, borrower.fb):
    for values in ([], [0,1,2,3], ['bad'], None):
        try:
            result = function(values)
            print(type(result).__name__,repr(result))
        except Exception as error:
            print(type(error).__name__,str(error))
"""
    quiet = _run_project(root, "import pkg.b_borrower")
    before = _run_project(root, driver)
    engine = UnificationRefactorEngine(
        strict=False,
        min_lines=3,
        skip_trivial_helpers=False,
        cross_module_helpers=True,
        excluded_directories=["_vendor"],
    )
    results, _ = engine.refactor_directory_to_fixed_point(
        str(root / "pkg"), str(root / "pkg"), progress="none"
    )
    assert sum(count for count, _ in results.values()) > 0
    assert _run_project(root, "import pkg.b_borrower") == quiet
    assert _run_project(root, driver) == before


def test_relaxed_namesake_imports(tmp_path: Path) -> None:
    from tests import test_required_namesakes as namesakes

    files = {
        **namesakes._LACKING_AUDITED,
        "pyproject.toml": '[project]\nname="zzapp"\nversion="0"\n',
        "zzapp/main.py": "from zzapp.cli import use\n",
        "tools/stale.py": "from zzclick.core import Command\n",
    }
    root = namesakes._write(tmp_path / "project", files)

    def observed(tree: Path) -> str:
        return _run_project(
            tree,
            f"sys.path.insert(0,{str(tree / 'third_party')!r}); "
            "import zzapp.cli as c; print('use(2) ->',c.use(2))",
        )

    before = observed(root)
    refused = namesakes._towel(root, ".", "--no-strict")
    assert refused.returncode == 1
    assert LACKING_MODULE_REMEDY in refused.stderr
    ran = namesakes._towel(
        root, ".", "--exclude", "tools", "--parameterize-builtins", "--no-strict"
    )
    assert ran.returncode == 0, ran.stderr
    assert observed(tmp_path / "project-out") == before


@pytest.mark.parametrize("stub", ["alpha/a.pyi", "alpha-stubs/a.pyi"])
def test_relaxed_stubbed_host(tmp_path: Path, stub: str) -> None:
    from tests import test_stubbed_host as stubbed

    files = {
        "pyproject.toml": '[project]\nname="alpha"\nversion="0"\n',
        "src/alpha/__init__.py": "",
        "src/alpha/py.typed": "",
        "src/alpha/a.py": "import alpha.b\n" + stubbed._function("fa", "a", 1),
        "src/alpha/b.py": stubbed._function("fb", "bb", 2),
        f"src/{stub}": "def fa(items: list[int], scale: int) -> int: ...\n",
    }
    if "-stubs" in stub:
        files["src/alpha-stubs/py.typed"] = "partial\n"
    stubbed._write(tmp_path, files)
    driver = "from alpha import a,b; print(a.fa([1,-2],2),b.fb([1,-2],3))"
    before = _run_project(tmp_path / "src", driver)
    assert _transform_project(tmp_path / "src" / "alpha") > 0
    assert _run_project(tmp_path / "src", driver) == before
    assert stubbed._mypy_errors(tmp_path) == []


@pytest.mark.parametrize("dirty", [False, True])
def test_relaxed_unconfigured_mypy(tmp_path: Path, dirty: bool) -> None:
    from tests import test_unconfigured_mypy as unconfigured

    source = textwrap.dedent(unconfigured.TWINS).lstrip()
    if dirty:
        source += "\nWRONG: str = first([1])\n"
    files = {
        "pyproject.toml": unconfigured.PACKAGING,
        "pkg/__init__.py": "",
        "pkg/core.py": source,
        "tests/test_core.py": unconfigured.UNCHECKED_TEST,
    }
    unconfigured._write(tmp_path, files)
    driver = "from pkg.core import first,second; print(first([1,2]),second([1,2]))"
    before = _run_project(tmp_path, driver)
    diagnostics = unconfigured._plain_mypy(tmp_path, ("pkg",))
    from tests.test_cli_integration import invoke

    ran = invoke(
        [
            "dry",
            str(tmp_path / "pkg"),
            str(tmp_path / "pkg"),
            "--no-strict",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
        ]
    )
    assert ran.status == 0, ran.stdout + ran.stderr
    assert (tmp_path / "pkg/core.py").read_text() != source
    assert _run_project(tmp_path, driver) == before
    after = unconfigured._plain_mypy(tmp_path, ("pkg",))
    assert [message for _, _, message in after[1]] == [message for _, _, message in diagnostics[1]]
    assert (tmp_path / "tests/test_core.py").read_text() == textwrap.dedent(
        unconfigured.UNCHECKED_TEST
    ).lstrip()


def test_relaxed_unparsed_instrumentation_project(tmp_path: Path) -> None:
    project = unparsed._project(tmp_path, {"pkg/fast.py": unparsed.NEWER + unparsed.HAND_APPLIED})
    kernels = project / "pkg/kernels.py"
    source = kernels.read_text()
    before = unparsed._kernel_results(source)
    engine = UnificationRefactorEngine(strict=False, min_lines=3, skip_trivial_helpers=False)
    proposals = engine.analyze_files([str(kernels)], progress="none")
    assert proposals
    for proposal in proposals:
        result = engine.apply_refactoring(str(kernels), proposal)
        assert unparsed._kernel_results(result) == before
    assert (project / "pkg/fast.py").read_text() == unparsed.NEWER + unparsed.HAND_APPLIED


def test_relaxed_unsupported_layout_cli(tmp_path: Path) -> None:
    from tests import test_unsupported_layout as unsupported
    from tests.test_cli_integration import invoke

    unsupported._hatch_project(tmp_path / "proj")
    before = unsupported._behavior(tmp_path / "proj")
    result = invoke(
        [
            "dry",
            str(tmp_path / "proj"),
            str(tmp_path / "out"),
            "--no-strict",
            "--no-types",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result.stderr
    transformed = (tmp_path / "out/pkg/a.py").read_text()
    assert "return f1(b)" in transformed
    assert "__extracted_func" not in transformed
    assert unsupported._behavior(tmp_path / "out") == before


def test_relaxed_pyright_excluded_runtime(tmp_path: Path) -> None:
    from tests import test_pyright_scope as scope

    scope._files(
        tmp_path,
        {
            "pyrightconfig.json": '{"exclude":["pkg/legacy"]}',
            "pkg/__init__.py": "",
            "pkg/legacy/__init__.py": "",
            "pkg/legacy/version.py": scope.VERSION,
            "pkg/core.py": "from pkg.legacy.version import Version\nV=Version((1,),None)\n",
        },
    )
    driver = "from pkg.legacy.version import Version; v=Version((1,2,3),'abc'); print(v.describe('v'),v.describe_short('v'))"
    before = _run_project(tmp_path, driver)
    oracle = PyrightOracle()
    try:
        engine = UnificationRefactorEngine(
            strict=False, min_lines=3, skip_trivial_helpers=False, type_oracle=oracle
        )
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path / "pkg"), str(tmp_path / "pkg"), progress="none"
        )
        assert sum(count for count, _ in results.values()) > 0
    finally:
        oracle.close()
    assert _run_project(tmp_path, driver) == before


@pytest.mark.parametrize("dirty", [False, True])
def test_relaxed_copied_directory_type_policy(tmp_path: Path, dirty: bool) -> None:
    from tests import test_type_run_policy as policy

    root = tmp_path / "project"
    source_root = root / "src"
    source_root.mkdir(parents=True)
    (root / "mypy.ini").write_text("[mypy]\nstrict=true\nfiles=src,consumer.py\n")
    (source_root / "__init__.py").write_text("")
    source = policy._source()
    path = source_root / "program.py"
    path.write_text(source)
    consumer = (
        'broken: int = "wrong"\n'
        if dirty
        else f"from src.program import first\nvalue: int=first({'1' if _supported_runtime() else ''})\n"
    )
    (root / "consumer.py").write_text(consumer)
    arguments = ("3", "5") if _supported_runtime() else ("", "")
    driver = (
        f"from program import first,second; print(first({arguments[0]}),second({arguments[1]}))"
    )
    before = _run_project(source_root, driver)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(strict=False, type_oracle=oracle)
        results, _ = engine.refactor_directory_to_fixed_point(
            str(source_root), str(tmp_path / "output"), progress="none"
        )
        assert sum(count for count, _ in results.values()) > 0
        assert engine.type_oracle is oracle
        assert not isinstance(oracle.check(str(path), source), CheckFailure)
    finally:
        oracle.close()
    assert _run_project(tmp_path / "output", driver) == before
    assert path.read_text() == source
    assert (root / "consumer.py").read_text() == consumer


def test_relaxed_fallback_annotation_runtime(tmp_path: Path) -> None:
    from tests import test_type_inference as inference

    source = _TYPED_RETURN.replace("OTHER", "int")
    driver = "print(first(30),second(7))"
    before = _observe(source, driver)
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        strict=False,
        min_lines=2,
        skip_trivial_helpers=False,
        reuse_existing_functions=False,
        type_oracle=inference._Oracle(),
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert "_typing.Any" in result
    assert _observe(result, driver) == before


def test_relaxed_correlated_returns(tmp_path: Path) -> None:
    source = """
from typing import NamedTuple
class Span(NamedTuple):
    start: int
    end: int
_spans: list[Span] = []
def highlight_regex() -> int:
    append_span = _spans.append
    _Span = Span
    plain = "abc"
    append_span(_Span(0, len(plain)))
    return len(_spans)
def highlight_words() -> int:
    add_span = _spans.append
    count = 0
    _Span = Span
    add_span(_Span(count, count + 1))
    return count
"""
    driver = "print(highlight_regex(),highlight_words(),[tuple(s) for s in _spans])"
    before = _observe(source, driver)
    result = _transform(tmp_path / "m.py", source, typed=True)
    assert _observe(result, driver) == before


@pytest.mark.parametrize("kind", ["siblings", "method-function", "identical"])
def test_relaxed_shared_helpers(tmp_path: Path, kind: str) -> None:
    if kind == "siblings":
        source = """
class Base: pass
class First(Base):
    offset = 3
    def alpha(self,value):
        tmp=value+self.offset
        return tmp*2
class Second(Base):
    offset = 7
    def beta(self,value):
        tmp=value+self.offset
        return tmp*2
"""
        driver = "print(First().alpha(2),Second().beta(4))"
    elif kind == "method-function":
        source = """
def alpha(value):
    tmp=value+1
    total=tmp*2
    return total
class K:
    def m(self,value):
        tmp=value+1
        total=tmp*2
        return total
"""
        driver = "print(alpha(3),K().m(5))"
    else:
        from tests import test_reuse_existing_function as reuse

        source = reuse.IDENTICAL_PAIR
        driver = "print(alpha(3),beta(5))"
    source = textwrap.dedent(source).lstrip()
    before = _observe(source, driver)
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, driver) == before


def test_relaxed_out_of_place_cycle(tmp_path: Path) -> None:
    from tests import test_out_of_place_cycle_regression as cycle

    root = tmp_path / "ready"
    pkg = root / "app"
    files = {
        "app/__init__.py": "",
        "app/a.py": "from app.b import BASE\n\ndef a1(seq,factor):\n"
        + cycle._SHARED
        + "\ndef a2(seq,factor):\n"
        + cycle._SHARED,
        "app/b.py": "BASE=1\n\ndef b1(seq,factor):\n"
        + cycle._SHARED
        + "\ndef b2(seq,factor):\n"
        + cycle._SHARED,
    }
    builtin_arguments._write(root, files)
    driver = "from app import a,b; print([f([1,2],3) for f in (a.a1,a.a2,b.b1,b.b2)])"
    before = _run_project(root, driver)
    engine = UnificationRefactorEngine(strict=False, cross_module_helpers=True)
    output = tmp_path / "elsewhere/app"
    results, _ = engine.refactor_directory_to_fixed_point(str(pkg), str(output), progress="none")
    assert sum(count for count, _ in results.values()) > 0
    # Adopt the generated files, without deleting the original package directory.
    for path in output.rglob("*.py"):
        target = pkg / path.relative_to(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
    assert _run_project(root, driver) == before


def _observe_arguments(script: Path, arguments: list[str]) -> str:
    ran = subprocess.run(
        [sys.executable, "-B", str(script), *arguments], capture_output=True, text=True, timeout=30
    )
    assert ran.returncode == 0, ran.stderr
    assert not ran.stderr
    return ran.stdout


@pytest.mark.parametrize("case", thunk_ordering._CASES, ids=lambda case: case.name)
def test_relaxed_assignment_ordering(tmp_path: Path, case: thunk_ordering.OrderingCase) -> None:
    from tests import test_thunk_ordering_cli as ordering
    from tests.test_cli_integration import invoke

    assert isinstance(case, ordering.OrderingCase)
    project = tmp_path / "project"
    project.mkdir()
    source = ordering._source(case)
    (project / "case.py").write_text(source)
    output = tmp_path / "output"
    driver = tmp_path / "observe.py"
    driver.write_text(ordering._DRIVER.replace("EVENTS.append(('del', self.label))", "pass"))
    names = '["left","right","third"]' if len(case.statements) == 3 else '["left","right"]'
    before = [
        _observe_arguments(driver, [str(project), mode, names, "cold", case.name])
        for mode in ordering._MODES
    ]
    ran = invoke(
        [
            "dry",
            str(project),
            str(output),
            "--no-strict",
            "--no-types",
            "--no-format",
            "--no-interactive",
            "--max-parameters",
            "5",
            "--min-lines",
            "3",
            "--progress",
            "none",
        ]
    )
    assert ran.status == 0, ran.stderr
    if not case.refusal:
        assert (output / "case.py").read_text() != source
    after = [
        _observe_arguments(driver, [str(output), mode, names, "cold", case.name])
        for mode in ordering._MODES
    ]
    assert after == before


@pytest.mark.parametrize("name,left,right,tail", thunk_factories._CASES)
def test_relaxed_factory_effects(
    tmp_path: Path, name: str, left: str, right: str, tail: str
) -> None:
    from tests import test_thunk_result_lifetimes as factories

    source = "def left(d):\n" + left + thunk_ordering._MARKER + tail
    source += "\ndef right(d):\n" + right + thunk_ordering._MARKER + tail
    project = tmp_path / "before"
    project.mkdir()
    path = project / "case.py"
    path.write_text(source)
    output = tmp_path / "after"
    output.mkdir()
    result = _transform(path, source)
    (output / "case.py").write_text(result)
    driver = tmp_path / "observe.py"
    observer = factories._DRIVER.replace("EVENTS.append(('del', self.label))", "pass")
    observer = observer.replace("EVENTS.append(('del', 'destination'))", "pass")
    driver.write_text(observer)
    for mode in factories._MODES:
        assert _observe_arguments(driver, [str(output), mode]) == _observe_arguments(
            driver, [str(project), mode]
        )


def test_relaxed_builtin_supplier_effects(tmp_path: Path) -> None:
    body = (
        "    size=len(values)+1\n    result=size*2\n    total=result+3\n    return total*FACTOR\n"
    )
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def first(values):\n" + body.replace("FACTOR", "5"),
        "pkg/b.py": "import pkg.a\ndef second(values):\n" + body.replace("FACTOR", "7"),
    }
    builtin_arguments._write(tmp_path, files)
    driver = """
from pkg import a,b
EVENTS=[]
class Number:
    def __init__(self,value): self.value=value
    def __add__(self,value):
        EVENTS.append(('add',self.value,value))
        if MODE=='add-error': raise ArithmeticError('add')
        return Number(self.value+value)
    def __mul__(self,value):
        EVENTS.append(('mul',self.value,value))
        if MODE=='mul-error': raise ArithmeticError('mul')
        return Number(self.value*value)
class Length:
    def __init__(self,module): self.module=module
    def __call__(self,values):
        EVENTS.append('length')
        del self.module.len
        return Number(4)
for MODE in ('ok','add-error','mul-error'):
    for module,name in ((a,'first'),(b,'second')):
        EVENTS.clear()
        module.len=Length(module)
        try:
            EVENTS.append(('result',getattr(module,name)([1,2]).value))
        except ArithmeticError as error:
            EVENTS.append(('error',str(error)))
        print(EVENTS)
"""
    before = _run_project(tmp_path, driver)
    assert _transform_project(tmp_path / "pkg") > 0
    assert _run_project(tmp_path, driver) == before


@pytest.mark.parametrize("typed", [False, True])
def test_relaxed_cli_typing_and_builtin_flag(tmp_path: Path, typed: bool) -> None:
    from tests.test_cli_integration import invoke
    from tests import test_type_run_policy as policy

    source = policy._source()
    root = tmp_path / "source"
    root.mkdir()
    (root / "m.py").write_text(source)
    arguments = ("1", "5") if _supported_runtime() else ("", "")
    driver = f"from m import first,second; print(first({arguments[0]}),second({arguments[1]}))"
    before = _run_project(root, driver)
    output = tmp_path / "output"
    args = [
        "dry",
        str(root),
        str(output),
        "--no-strict",
        "--no-interactive",
        "--no-format",
        "--parameterize-builtins",
        "--progress",
        "none",
    ]
    if not typed:
        args.append("--no-types")
    ran = invoke(args)
    assert ran.status == 0, ran.stderr
    assert "__extracted_func" in (output / "m.py").read_text()
    assert _run_project(output, driver) == before

    # The flag must also control the mixed builtin/local binding case.
    mixed = parameterized_builtins.MIXED.replace(
        "    area = width * height",
        "    area = width * height\n"
        + "".join(
            f"    area = area + {number}\n    area = area - {number}\n" for number in range(1, 10)
        ).rstrip(),
    )
    for flag in (False, True):
        target = tmp_path / ("enabled" if flag else "disabled")
        target.mkdir()
        (target / "m.py").write_text(mixed)
        before = _run_project(
            target, "from m import first,second; print(first([1,2],'a'),second([3],'b'))"
        )
        args = [
            "dry",
            str(target),
            str(target),
            "--no-strict",
            "--no-types",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
        ]
        if flag:
            args.append("--parameterize-builtins")
        ran = invoke(args)
        assert ran.status == 0, ran.stderr
        assert (
            _run_project(
                target, "from m import first,second; print(first([1,2],'a'),second([3],'b'))"
            )
            == before
        )
        if flag:
            assert "__extracted_func" in (target / "m.py").read_text()


@pytest.mark.parametrize("kind", ["mixed-parameters", "instance", "class"])
def test_relaxed_profitability_returns(tmp_path: Path, kind: str) -> None:
    body = "".join(f"    {chr(98+i)} = {chr(97+i)} + {i+2}\n" for i in range(10))
    if kind == "mixed-parameters":
        source = "def first(unused):\n    a=1\n" + body + "    return k\n"
        source += "def second():\n    a=1\n" + body + "    return k\n"
        driver = "print(first(object()),second())"
    else:
        receiver_name = "cls" if kind == "class" else "self"
        decorator = "    @classmethod\n" if kind == "class" else ""
        source = "class C:\n    base=1\n" + "".join(
            decorator + f"    def {name}({receiver_name}):\n"
            f"        a={receiver_name}.base+{offset}\n"
            + textwrap.indent(body, "    ")
            + "        return k\n"
            for name, offset in (("first", 1), ("second", 2))
        )
        driver = "print(C().first(),C().second())"
    before = _observe(source, driver)
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, driver) == before


def test_relaxed_returned_local_binding(tmp_path: Path) -> None:
    from tests import test_only_binding_moves as bindings

    source = bindings.REPRODUCER.replace("len(items))", "total)").replace("len(rows))", "total)")
    driver = """
for items in ([],[1,2]):
    for function in (summarize_a,summarize_b):
        try:
            print(function(items))
        except UnboundLocalError:
            print('unbound')
"""
    before = _observe(source, driver)
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, driver) == before


def test_relaxed_matrix_layout(tmp_path: Path) -> None:
    source = (
        Path(__file__).parent / "hostile_cases/r9dr_fmt_off_layout_moves_verbatim.py"
    ).read_text()
    before = _observe(source, "")
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, "") == before
    assert "1,   0,   0," in result
    assert "count += row  *  2  # fmt: skip" in result


def test_relaxed_excluded_instrumenter(tmp_path: Path) -> None:
    from tests.test_cli_integration import invoke

    root = tmp_path / "project"
    files = {
        "pkg/__init__.py": "",
        "pkg/kernels.py": unparsed.KERNELS,
        "extras/fast.py": unparsed.HAND_APPLIED,
        "pyproject.toml": '[project]\nname="project"\nversion="0"\n',
    }
    builtin_arguments._write(root, files)
    before = unparsed._kernel_results(unparsed.KERNELS)
    output = tmp_path / "output"
    ran = invoke(
        [
            "dry",
            str(root),
            str(output),
            "--exclude",
            "extras",
            "--no-strict",
            "--no-types",
            "--no-interactive",
            "--no-format",
            "--progress",
            "none",
        ]
    )
    assert ran.status == 0, ran.stderr
    assert unparsed._kernel_results((output / "pkg/kernels.py").read_text()) == before
    assert (root / "extras/fast.py").read_text() == unparsed.HAND_APPLIED


@pytest.mark.parametrize(
    "case",
    [
        "property",
        "subscript",
        "operator",
        "context_target",
        "iteration_target",
        "nested_if",
        "caught_exception",
        "explicit_call_control",
    ],
)
@pytest.mark.parametrize("typed", [False, True])
def test_relaxed_protocol_results(tmp_path: Path, case: str, typed: bool) -> None:
    source = (Path(__file__).parent / f"hostile_cases/r1792_protocol_{case}.py").read_text()
    # The original protocol fixtures also assert ordinary __exit__, caught
    # exception and continuation effects. Only their finalizer logging is
    # omitted here; all those ordinary effects remain in exact sequence.
    source = source.replace("events.append('drop:' + self.label)", "pass")
    before = _observe(source, "")
    path = tmp_path / "m.py"
    path.write_text(source)
    oracle = MypyInferrer() if typed else None
    try:
        engine = UnificationRefactorEngine(
            strict=False, skip_trivial_helpers=False, annotate_helpers=typed, type_oracle=oracle
        )
        result, _, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        if oracle is not None:
            oracle.close()
    assert _observe(result, "") == before


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("no_parameters", [False, True])
def test_relaxed_finalizer_fixture_ordinary_effects(
    tmp_path: Path, alias: bool, no_parameters: bool
) -> None:
    source = (Path(__file__).parent / "hostile_cases/r1792_factory_finalizer_order.py").read_text()
    source = source.replace("events.append(self.name)", "pass")
    if alias:
        source = source.replace('    a = acquire("a")', '    a = acquire("a")\n    copy_z = z')
    if no_parameters:
        source = (
            source.replace("first(value: int)", "first()")
            .replace("second(value: int)", "second()")
            .replace("p = value + 1", "p = 2")
            .replace("p = value + 2", "p = 3")
            .replace("first(1), second(1)", "first(), second()")
        )
    before = _observe(source, "")
    result = _transform(tmp_path / "m.py", source)
    assert _observe(result, "") == before
