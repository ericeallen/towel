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

"""Code the type checker does not look at is not changed, since no check of it says anything.

A checker takes code under a ``sys.platform`` or ``sys.version_info`` test its
platform and Python make false, or after an ``assert`` it knows fails, to be
unreachable, and reports nothing there. Towel accepted a helper in trio's
``_windows_pipes.py``, which asserts win32, on a checker running elsewhere, and
trio's own win32 check rejected it. Each checker is asked where it looks, by a
``reveal_type`` probe it answers only there; a change that writes a line some
checker does not answer at is declined as not verifiable, and the regions are
named before the run.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
import textwrap

import pytest

from towel.type_inference import CombinedOracle, MypyInferrer, PyrightOracle
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

UNCHECKED = "not verifiable: the type checker does not look at the code it changes"

BLOCK = """
    total = value + 1
    doubled = total * 2
    answer = doubled - 3
    return answer
"""


def _twins(indent: str = "") -> str:
    """Two functions sharing ``BLOCK``, each body indented by ``indent`` more."""
    block = textwrap.indent(textwrap.dedent(BLOCK).strip("\n"), "    " + indent)
    return f"def first(value: int) -> int:\n{block}\n\n\ndef second(value: int) -> int:\n{block}\n"


def _run(
    tmp_path: Path, source: str, oracle: object, config: str = "[tool.mypy]\nstrict = true\n"
) -> tuple[str, int, UnificationRefactorEngine]:
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)  # type: ignore[arg-type]
    _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    return path.read_text(encoding="utf-8"), applied, engine


@pytest.mark.looks_nowhere
@requires_mypy
def test_a_module_that_asserts_another_platform_is_left_alone_and_named(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Towel's host is never win32 (it runs on POSIX), so mypy takes all of this as unreachable."""
    source = (
        "import sys\nfrom typing import TYPE_CHECKING\n\n"
        'assert sys.platform == "win32" or not TYPE_CHECKING\n\n\n' + _twins()
    )
    oracle = MypyInferrer()
    caplog.set_level(logging.WARNING, logger="towel")
    try:
        written, applied, engine = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert applied == 0 and written == source
    assert engine.run_report.declined_proposals == {UNCHECKED: 1}
    assert "the type checker does not look at 1 region(s)" in caplog.text
    assert "m.py:7-18" in caplog.text


@requires_mypy
def test_code_the_declared_types_rule_out_is_left_alone_without_blaming_the_platform(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """packaging's shape: no platform checks a branch an annotation says is never taken."""
    block = textwrap.indent(textwrap.dedent(BLOCK).strip("\n"), " " * 12)
    source = "".join(
        f"def {name}(value: int, other: int) -> int:\n"
        f"    if not isinstance(other, int):\n{block}\n"
        f"    return {result}\n\n\n"
        for name, result in (("first", 1), ("second", 2))
    )
    oracle = MypyInferrer()
    caplog.set_level(logging.DEBUG, logger="towel")
    try:
        written, applied, engine = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert applied == 0 and written == source
    warning = next(
        record.getMessage()
        for record in caplog.records
        if "the type checker does not look at" in record.getMessage()
    )
    assert "because the declared types rule them out" in warning
    assert "another platform" not in warning


def _guarded(test: str) -> str:
    """``BLOCK`` in both functions, under ``test``, and a different statement after it."""
    return textwrap.dedent(f"""
        import sys


        def first(value: int) -> int:
            if {test}:
                total = value + 1
                doubled = total * 2
                answer = doubled - 3
                return answer
            return 0


        def second(value: int) -> int:
            if {test}:
                total = value + 1
                doubled = total * 2
                answer = doubled - 3
                return answer
            raise OSError("unsupported")
        """).lstrip()


@pytest.mark.looks_nowhere
@requires_mypy
def test_a_branch_under_another_platform_inside_a_function_is_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = _guarded('sys.platform == "win32"')
    oracle = MypyInferrer()
    caplog.set_level(logging.WARNING, logger="towel")
    try:
        written, applied, engine = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert applied == 0 and written == source, written
    assert engine.run_report.declined_proposals == {UNCHECKED: 1}
    assert "m.py:6-9" in caplog.text and "m.py:15-18" in caplog.text


@requires_mypy
def test_the_branch_the_checker_does_look_at_is_refactored(tmp_path: Path) -> None:
    """The control: the same blocks under the ``else`` of the same test are checked, and move.

    The two ``if`` bodies differ, so the duplicate is the ``else`` body alone,
    which the checker looks at, beside a branch it does not.
    """
    source = textwrap.dedent("""
        import sys


        def first(value: int) -> int:
            if sys.platform == "win32":
                return 0
            else:
                total = value + 1
                doubled = total * 2
                answer = doubled - 3
                return answer


        def second(value: int) -> int:
            if sys.platform == "win32":
                raise OSError("unsupported")
            else:
                total = value + 1
                doubled = total * 2
                answer = doubled - 3
                return answer
        """).lstrip()
    oracle = MypyInferrer()
    try:
        written, applied, engine = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert applied == 1 and "def __extracted_func_0(value: int) -> int:" in written, written
    assert 'if sys.platform == "win32":\n        return 0' in written
    assert UNCHECKED not in engine.run_report.declined_proposals


@pytest.mark.looks_nowhere
@requires_mypy
def test_a_branch_for_a_python_the_checker_does_not_check_for_is_left_alone(
    tmp_path: Path,
) -> None:
    source = _guarded("sys.version_info < (3, 8)")
    oracle = MypyInferrer()
    try:
        written, applied, engine = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert applied == 0 and written == source, written
    assert engine.run_report.declined_proposals == {UNCHECKED: 1}


@pytest.mark.looks_nowhere
@requires_mypy
@requires_pyright
def test_every_checker_is_asked_where_it_looks_before_a_change_is_accepted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pyright, set to check for Windows, skips what mypy, checking for the host, looks at.

    mypy infers, so the regions named before the run are mypy's and name none
    here; the change is still refused, because pyright was asked too.
    """
    source = _guarded('sys.platform != "win32"')
    config = (
        "[tool.mypy]\nstrict = true\n\n"
        '[tool.pyright]\npythonPlatform = "Windows"\ntypeCheckingMode = "basic"\n'
    )
    oracle = CombinedOracle(MypyInferrer(), [PyrightOracle()])
    caplog.set_level(logging.WARNING, logger="towel")
    try:
        written, applied, engine = _run(tmp_path, source, oracle, config)
    finally:
        oracle.close()
    assert "does not look at" not in caplog.text.split("Dropped")[0], "mypy looks at all of it"
    assert applied == 0 and written == source, written
    assert engine.run_report.declined_proposals == {UNCHECKED: 1}


@pytest.mark.looks_nowhere
@requires_mypy
def test_dead_code_the_regions_before_the_run_miss_is_refused_by_the_check_itself(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """After an ``if`` whose branches both return, nothing runs, and mypy looks at none of it.

    Such code starts no block, so no region names it before the run; the
    change is asked about the lines it writes, and refused there.
    """
    source = textwrap.dedent("""
        def first(value: int) -> int:
            if value:
                return 1
            else:
                return 2
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        def second(value: int) -> int:
            if value > 3:
                raise ValueError(value)
            else:
                return 3
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer
        """).lstrip()
    oracle = MypyInferrer()
    caplog.set_level(logging.WARNING, logger="towel")
    try:
        written, applied, engine = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert "does not look at" not in caplog.text.split("Dropped")[0]
    assert applied == 0 and written == source, written
    assert engine.run_report.declined_proposals == {UNCHECKED: 1}


# --- Where the probes go --------------------------------------------------------


def test_a_one_line_body_is_probed_on_a_line_of_its_own() -> None:
    from towel.reachability import probe_plan

    plan = probe_plan(
        "import sys\n"
        'if sys.platform == "win32": x = 1; y = 2\n'
        'elif sys.platform == "darwin": z = 3\n'
        "else: w = 4\n"
    )
    assert plan is not None
    assert plan.text.split("\n")[1:7] == [
        'if sys.platform == "win32":',
        "    x = 1; y = 2",
        'elif sys.platform == "darwin":',
        "    z = 3",
        "else:",
        "    w = 4",
    ]
    assert plan.sites[(2, 28)] == plan.sites[(2, 35)] == (3, "    "), "a ; sibling shares one"
    assert (3, 0) not in plan.sites, "nothing can stand before an elif"
    assert plan.sites[(3, 31)] == (5, "    ") and plan.sites[(4, 6)] == (7, "    ")


def test_a_decorated_definition_is_probed_before_its_first_decorator() -> None:
    from towel.reachability import probe_plan

    plan = probe_plan("import functools\n\n\n@functools.cache\n@staticmethod\ndef f(): pass\n")
    assert plan is not None and plan.sites[(4, 0)] == (4, "")


def test_nothing_is_probed_before_a_docstring_or_a_future_import() -> None:
    from towel.reachability import probe_plan

    plan = probe_plan('"""Doc."""\nfrom __future__ import annotations\nimport os\n')
    assert plan is not None
    assert (1, 0) not in plan.sites and (2, 0) not in plan.sites
    assert plan.sites[(3, 0)] == (3, "")


def test_the_statements_after_an_assert_are_a_block_of_their_own() -> None:
    from towel.reachability import probe_plan

    plan = probe_plan("import sys\nassert sys.platform == 'win32'\nx = 1\ny = 2\n")
    assert plan is not None
    assert ((3, 0), 3, 4) in plan.blocks and ((1, 0), 1, 4) in plan.blocks


def test_a_class_body_is_answered_for_by_its_class() -> None:
    """A probe is no statement a TypedDict body may hold, so the class answers for its body."""
    from towel.reachability import probe_plan

    source = (
        "import typing\n\n\nclass Info(typing.TypedDict):\n    name: str\n\n\n"
        "class More(Info):\n    size: int\n\n    def f(self) -> None:\n        pass\n"
    )
    plan = probe_plan(source)
    assert plan is not None
    assert plan.sites[(5, 4)] == plan.sites[(4, 0)] == (4, "")
    assert plan.sites[(9, 4)] == plan.sites[(11, 4)] == plan.sites[(8, 0)]
    assert plan.sites[(12, 8)] == (12, "        "), "a method's body is probed where it stands"


@requires_mypy
def test_a_typed_dict_is_no_region_the_checker_skips(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = "import typing\n\n\nclass Info(typing.TypedDict):\n    name: str\n\n\n" + _twins()
    oracle = MypyInferrer()
    caplog.set_level(logging.WARNING, logger="towel")
    try:
        written, applied, _ = _run(tmp_path, source, oracle)
    finally:
        oracle.close()
    assert "does not look at" not in caplog.text
    assert applied == 1, written


def test_a_module_that_does_not_parse_is_one_nothing_is_known_about() -> None:
    from towel.reachability import probe_plan

    assert probe_plan("def broken(:\n") is None
