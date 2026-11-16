# Copyright 2026 Eric Allen
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

"""What relaxed mode, the default, does differently from strict mode, and what both keep.

The user-perspective audit of 1.949 found that the lifetime rules decide what
a first run looks like. Finding 1: the quick start's callers carry
``_towel_arguments = [(amount,)]``, ``del amount`` and a ``pop()``. Finding
2: the same rules decline most blocks short of a whole function body. Relaxed
mode assumes a program does not depend on when its objects are finalized
(docs/design/DECISIONS.md, "Relaxed mode is the default; strict mode keeps exact
lifetimes"), so its callers are plain calls; strict mode is 1.949 exactly.

The scenario fixtures are the audit's designed cases, copied under
``tests/fixtures/relaxed_scenarios`` with their drivers.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification import argument_ownership
from towel.unification.block_analysis import first_assignment_order
from tests.test_cli_integration import invoke

# The default mode is what this module pins.
pytestmark = pytest.mark.relaxed_mode

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "tests" / "fixtures" / "relaxed_scenarios"
UNTYPED = ["--no-types", "--no-format", "--progress", "none"]

NOISY = """\
class Noisy:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        print("released", self.name)


def log(message):
    print(message)


"""


def _mode(strict: bool) -> str:
    return "--strict" if strict else "--no-strict"


def _output(script: Path, *arguments: str) -> str:
    """What running ``script`` prints; it must succeed."""
    result = subprocess.run(
        [sys.executable, str(script), *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=script.parent,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _refactored(tmp_path: Path, source: str, *, strict: bool) -> Tuple[str, str, str]:
    """``source`` refactored in ``strict`` or relaxed mode: its text, and the output before and after.

    Small safe transformations are allowed (``skip_trivial_helpers=False``),
    as in the hostile batteries, so that an example can be a few lines long.
    """
    original = tmp_path / "original.py"
    original.write_text(source)
    changed = tmp_path / ("strict.py" if strict else "relaxed.py")
    changed.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_to_fixed_point(str(changed))
    return changed.read_text(), _output(original), _output(changed)


def _release_order_certified() -> bool:
    """Whether strict mode's lifetime proofs hold here: CPython 3.11 to 3.13.

    The tests below choose their mode, so they read the predicate itself
    (``tests.test_helpers.lifetime_order_certified``). On 3.14 and later
    CPython releases a frame's variables in reverse order, and strict mode
    declines what its proofs would extract (``lifetime_order_uncertified``).
    """
    return argument_ownership._supported_runtime()


def _strict_declines_for_release_order(tmp_path: Path, source: str) -> None:
    """On an uncertified runtime strict mode leaves ``source`` alone, saying why."""
    path = tmp_path / "declined.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_to_fixed_point(str(path))
    assert path.read_text() == source
    assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0


def _body(source: str, function: str) -> str:
    """The statements of ``function``'s body, unparsed."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return "\n".join(ast.unparse(statement) for statement in node.body)
    raise AssertionError(f"no function {function}")


# -- Finding 1: the quick start ------------------------------------------------


def _quick_start() -> str:
    document = (ROOT / "docs" / "QUICKSTART.md").read_text()
    blocks: list[str] = re.findall(r"(?ms)^```python\n(.*?)^```", document)
    return blocks[0]


def test_the_quick_start_gets_three_one_line_callers(tmp_path: Path) -> None:
    """Audit finding 1: the quick start's ``charges.py`` reads as a person would write it."""
    source = tmp_path / "charges.py"
    source.write_text(_quick_start())
    output = tmp_path / "out.py"
    result = invoke(["dry", str(source), str(output), "--no-interactive", *UNTYPED])
    assert result.status == 0, result.stderr
    generated = output.read_text()
    for function in ("checkout_total", "quote_total", "invoice_total"):
        assert _body(generated, function) == "return __extracted_func_0(amount)"
    assert "_towel_" not in generated
    helper = next(
        node
        for node in ast.walk(ast.parse(generated))
        if isinstance(node, ast.FunctionDef) and node.name == "__extracted_func_0"
    )
    assert [argument.arg for argument in helper.args.args] == ["amount"]


def test_strict_mode_keeps_the_quick_starts_ownership_transfer(tmp_path: Path) -> None:
    """``--strict`` is 1.949: the argument's release order is kept by the box and the holder."""
    source = tmp_path / "charges.py"
    source.write_text(_quick_start())
    output = tmp_path / "out.py"
    result = invoke(["dry", str(source), str(output), "--no-interactive", "--strict", *UNTYPED])
    assert result.status == 0, result.stderr
    generated = output.read_text()
    if not _release_order_certified():
        assert generated == source.read_text()
        assert "[lifetime_order_uncertified]" in result.stdout
        return
    assert _body(generated, "checkout_total") == (
        "_towel_arguments = [(amount,)]\n"
        "del amount\n"
        "return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())"
    )
    assert "def __extracted_func_0(amount, _towel_owner):" in generated


# -- Findings 1 and 2: the audit's scenario cases ------------------------------

DECLINED_BY_1949 = (
    "literal_small",
    "varname_small",
    "callee_user",
    "block_begin",
    "block_middle",
    "methods_unrelated",
    "try_convert",
    "loop_accumulate",
    "near_reordered",
    "near_replaced",
    "expr_dict",
)
"""Audit cases 01, 04, 06, 10, 11, 15, 22, 23, 40, 41 and 45: 1.949 changed none of them."""

DECORATED_BY_1949 = ("pytest_parametrize", "copies4_functions")
"""Audit cases 17 and 35: 1.949 changed them, with ``_towel_`` names in the result."""

SHORTENED_BY_1949 = ("block_end",)
"""Audit case 12: 1.949 left the first statement of the five-line block behind."""


def _scenario(tmp_path: Path, name: str, *, strict: bool) -> Tuple[Path, Path, Path]:
    """The scenario's driver, its source directory, and that directory refactored in one mode."""
    case = tmp_path / name
    for fixture in sorted((SCENARIOS / name).rglob("*.py.txt")):
        target = case / fixture.relative_to(SCENARIOS / name).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture, target)
    source, output = case / "src", case / "out"
    result = invoke(["dry", str(source), str(output), "--no-interactive", _mode(strict), *UNTYPED])
    assert result.status == 0, result.stderr
    return case / "driver.py", source, output


def _python_text(directory: Path) -> str:
    return "\n".join(path.read_text() for path in sorted(directory.rglob("*.py")))


@pytest.mark.parametrize("name", DECLINED_BY_1949 + DECORATED_BY_1949 + SHORTENED_BY_1949)
def test_relaxed_mode_extracts_the_audits_cases_with_plain_calls(tmp_path: Path, name: str) -> None:
    """Audit findings 1 and 2: each case is changed, runs as before, and has no ``_towel_`` name."""
    driver, source, output = _scenario(tmp_path, name, strict=False)
    generated = _python_text(output)
    assert generated != _python_text(source)
    assert "_extracted_func_0(" in generated
    assert "_towel_" not in generated
    assert _output(driver, str(output)) == _output(driver, str(source))


VALUE_EXTRACTED_IN_STRICT_MODE = ("expr_dict",)
"""The case strict mode declines as statements and, since 2.0, extracts as an expression.

Audit case 45: two assignments of one dictionary to differently named
variables. A helper of the assignments would own the variable, which the
lifetime rules decline; a helper of the dictionary alone
(``towel.unification.expression_sites``) binds nothing, the assignment stays
in the caller, and no lifetime changes.
"""


@pytest.mark.parametrize(
    "name", [name for name in DECLINED_BY_1949 if name not in VALUE_EXTRACTED_IN_STRICT_MODE]
)
def test_strict_mode_still_leaves_the_declined_cases_alone(tmp_path: Path, name: str) -> None:
    """Strict mode is 1.949: the lifetime rules decline these, which is what the audit found."""
    driver, source, output = _scenario(tmp_path, name, strict=True)
    assert _python_text(output) == _python_text(source)


@pytest.mark.parametrize("name", VALUE_EXTRACTED_IN_STRICT_MODE)
def test_strict_mode_extracts_the_value_of_an_assignment_it_declines(
    tmp_path: Path, name: str
) -> None:
    """The binding stays in the caller, so the call is plain and nothing is released differently."""
    driver, source, output = _scenario(tmp_path, name, strict=True)
    generated = _python_text(output)
    assert "    payload = __extracted_func_0(order)\n" in generated
    assert "    summary = __extracted_func_0(order)\n" in generated
    assert "_towel_" not in generated and "lambda" not in generated
    assert _output(driver, str(output)) == _output(driver, str(source))


UNDECORATED_WHERE_UNCERTIFIED = ("copies4_functions",)
"""Strict mode on 3.14 and later declines the ownership transfer that put ``_towel_`` names
into this case, and extracts only the returned dictionary, which needs none."""


@pytest.mark.parametrize("name", DECORATED_BY_1949)
def test_strict_mode_still_decorates_the_cases_it_changes(tmp_path: Path, name: str) -> None:
    driver, source, output = _scenario(tmp_path, name, strict=True)
    generated = _python_text(output)
    if name in UNDECORATED_WHERE_UNCERTIFIED and not _release_order_certified():
        assert generated != _python_text(source)
        assert "_towel_" not in generated
    else:
        assert "_towel_" in generated
    assert _output(driver, str(output)) == _output(driver, str(source))


def test_relaxed_mode_takes_the_whole_block_strict_mode_cut_short(tmp_path: Path) -> None:
    """Audit case 12: the binding of ``message`` moves too, so ``message`` is no parameter."""
    _, _, relaxed = _scenario(tmp_path / "relaxed", "block_end", strict=False)
    _, _, strict = _scenario(tmp_path / "strict", "block_end", strict=True)
    assert "def __extracted_func_0(body, outbox, subject, user):" in _python_text(relaxed)
    assert "def __extracted_func_0(body, message, outbox, subject):" in _python_text(strict)


def test_returned_names_follow_the_order_of_first_assignment(tmp_path: Path) -> None:
    """Relaxed mode returns what a reader would write, not the alphabet (engine map A, item 4)."""
    _, _, output = _scenario(tmp_path, "block_middle", strict=False)
    generated = (output / "metrics.py").read_text()
    assert "return (count, mean, stddev, low, high)" in generated
    assert generated.count("count, mean, stddev, low, high = __extracted_func_0(values)") == 2


def test_first_assignment_order_reads_a_blocks_own_bindings() -> None:
    block = ast.parse(
        "b = 1\n"
        "for a in rows:\n"
        "    c = a\n"
        "b = 2\n"
        "def inner():\n"
        "    hidden = 1\n"
        "with open(p) as d:\n"
        "    pass\n"
    ).body
    assert first_assignment_order(block) == ("b", "a", "c", "inner", "d")


# -- What each mode preserves: the engine map's two examples -------------------

WHOLE_FUNCTION = NOISY + """\
def first(x):
    y = Noisy("local")
    total = len(y.name) + len(x.name)
    print("first", total)
    return total


def second(x):
    y = Noisy("local")
    total = len(y.name) + len(x.name)
    print("second", total)
    return total


if __name__ == "__main__":
    first(Noisy("argument"))
    second(Noisy("argument"))
"""


def test_relaxed_mode_calls_a_whole_body_helper_directly(tmp_path: Path) -> None:
    """The helper's frame releases its local before the caller releases the argument.

    Nothing moves relative to other code: both finalizers still run as the
    function returns. Only their order differs, which relaxed mode assumes
    no program depends on. On 3.14 and later the original frame releases in
    reverse order, local first, so there the two orders coincide.
    """
    generated, before, after = _refactored(tmp_path, WHOLE_FUNCTION, strict=False)
    assert _body(generated, "first") == "return __extracted_func_0('first', x)"
    assert "_towel_" not in generated
    if _release_order_certified():
        assert before.splitlines()[:3] == ["first 13", "released argument", "released local"]
    else:
        assert before.splitlines()[:3] == ["first 13", "released local", "released argument"]
    assert after.splitlines()[:3] == ["first 13", "released local", "released argument"]
    assert sorted(after.splitlines()) == sorted(before.splitlines())


def test_strict_mode_transfers_ownership_to_keep_the_release_order(tmp_path: Path) -> None:
    if not _release_order_certified():
        _strict_declines_for_release_order(tmp_path, WHOLE_FUNCTION)
        return
    generated, before, after = _refactored(tmp_path, WHOLE_FUNCTION, strict=True)
    assert "_towel_arguments = [(x,)]" in generated and "_towel_owner" in generated
    assert after == before


KEEP_HOLDER = NOISY + """\
def first():
    handle = Noisy("first handle")
    count = len(handle.name) + 1
    log(f"prepared {count}")
    log("after block")
    return count


def second():
    handle = Noisy("second handle")
    count = len(handle.name) + 2
    log(f"prepared {count}")
    if count:
        log("after block")
    return count


if __name__ == "__main__":
    first()
    second()
"""


def test_relaxed_mode_returns_only_what_later_code_reads(tmp_path: Path) -> None:
    """A local nothing reads again is released when the helper returns, before later code.

    This is a change of timing relative to the caller's later statements,
    which only a finalizer can observe; the resource guard covers the
    objects whose finalizer destroys something still in use.
    """
    generated, before, after = _refactored(tmp_path, KEEP_HOLDER, strict=False)
    assert "count = __extracted_func_0('first handle', 1)" in generated
    assert "_towel_" not in generated
    assert before.splitlines()[:3] == ["prepared 13", "after block", "released first handle"]
    assert after.splitlines()[:3] == ["prepared 13", "released first handle", "after block"]
    assert sorted(after.splitlines()) == sorted(before.splitlines())


def test_strict_mode_holds_an_unread_local_until_the_function_returns(tmp_path: Path) -> None:
    if not _release_order_certified():
        _strict_declines_for_release_order(tmp_path, KEEP_HOLDER)
        return
    generated, before, after = _refactored(tmp_path, KEEP_HOLDER, strict=True)
    assert "_towel_keep_handle, count = __extracted_func_0('first handle', 1)" in generated
    assert after == before


EXCEPTION_BOUNDARY = NOISY + """\
def first(values):
    try:
        handle = Noisy("first")
        total = sum(values)
        mean = total / len(values)
        print("first", handle.name, mean)
    except ZeroDivisionError:
        print("first: empty")
    print("first done")


def second(values):
    try:
        handle = Noisy("second")
        total = sum(values)
        mean = total / len(values)
        print("second", handle.name, mean)
    except ZeroDivisionError:
        print("second: empty")
    finally:
        print("second finally")
    print("second done")


if __name__ == "__main__":
    first([1, 2, 3])
    first([])
    second([4, 5])
    second([])
"""


def test_relaxed_mode_extracts_a_block_inside_a_handler_that_stays(tmp_path: Path) -> None:
    """No ``owned_binding_exception_boundary`` refusal: the same output but for when ``handle`` goes."""
    generated, before, after = _refactored(tmp_path, EXCEPTION_BOUNDARY, strict=False)
    assert "        __extracted_func_0('first', values)\n    except ZeroDivisionError:" in generated
    assert "_towel_" not in generated
    released = re.compile(r"^released ")
    assert [line for line in after.splitlines() if not released.match(line)] == [
        line for line in before.splitlines() if not released.match(line)
    ]
    assert sorted(after.splitlines()) == sorted(before.splitlines())


def test_strict_mode_refuses_a_block_whose_failure_would_release_early(tmp_path: Path) -> None:
    generated, before, after = _refactored(tmp_path, EXCEPTION_BOUNDARY, strict=True)
    assert generated == EXCEPTION_BOUNDARY


LEADING_THUNK = """\
class Stats:
    def __init__(self, total, count):
        self.total = total
        self.count = count


def describe_total(stats, label):
    value = stats.total
    scaled = value * 100
    text = f"{label}: {scaled}"
    print(text)
    return text


def describe_count(stats, label):
    value = stats.count
    scaled = value * 100
    text = f"{label}: {scaled}"
    print(text)
    return text


if __name__ == "__main__":
    describe_total(Stats(3, 4), "total")
    describe_count(Stats(3, 4), "count")
"""


def test_relaxed_mode_passes_a_leading_argument_by_value_whatever_its_result(
    tmp_path: Path,
) -> None:
    """Audit finding 1 (engine map A, item 5): evaluated first, once and always, so no lambda.

    The helper would call the thunk before anything else on every path, so
    evaluating the argument at the call changes no order. Strict mode still
    declines to hold an opaque result in a parameter for the helper's whole
    run, which only a finalizer could observe.
    """
    generated, before, after = _refactored(tmp_path, LEADING_THUNK, strict=False)
    assert _body(generated, "describe_total") == "return __extracted_func_0(stats.total, label)"
    assert "lambda" not in generated
    assert after == before


def test_strict_mode_keeps_the_thunk_of_an_opaque_result(tmp_path: Path) -> None:
    if not _release_order_certified():
        _strict_declines_for_release_order(tmp_path, LEADING_THUNK)
        return
    generated, before, after = _refactored(tmp_path, LEADING_THUNK, strict=True)
    assert "value = __param_0()" in generated and "lambda" in generated
    assert after == before
