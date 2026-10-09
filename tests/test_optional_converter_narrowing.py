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

"""Optional-aware conversions remain useful without erasing their types or effects."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal, Mapping, Sequence

import pytest

from tests.typed_fixtures import CountingMypy
import towel
from towel.type_inference import CheckResult, CheckSuccess, PyrightOracle
from towel.unification.exceptions import TypeRejectedExtraction, Untypeable, UntypeableExtraction
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


@dataclass(frozen=True)
class Fixture:
    name: str
    source: str
    input_kind: Literal["object", "text"] = "object"


POSITIVE_FIXTURES = (
    Fixture(
        "str-nullable",
        """import html
import re
import builtins


def escape(value: object | None, quote: bool = True) -> builtins.str:
    if value is not None:
        return html.escape(str(value), quote=quote)
    return ""


def detect(text: builtins.str) -> bool:
    if re.search("^yes", text, re.MULTILINE):
        return True
    return False
""",
        "object",
    ),
    Fixture(
        "str-nonnullable",
        """import html
import re
import builtins


def escape(value: builtins.str, quote: bool = True, enabled: bool = False) -> builtins.str:
    if enabled:
        return html.escape(str(value), quote=quote)
    return ""


def detect(text: builtins.str) -> bool:
    if re.search("^yes", text, re.MULTILINE):
        return True
    return False
""",
        "text",
    ),
    Fixture(
        "repr-consumer",
        """import html
import re
import builtins


def convert(value: object | None) -> builtins.str:
    return repr(value)


def escape(value: object | None, quote: bool = True) -> builtins.str:
    if value is not None:
        return html.escape(convert(value), quote=quote)
    return ""


def detect(text: builtins.str) -> bool:
    if re.search("^yes", text, re.MULTILINE):
        return True
    return False
""",
        "object",
    ),
)

NEGATIVE_FIXTURES = (
    Fixture(
        "int-required",
        """def escape(value: float | None) -> int:
    if value is not None:
        return int(value)
    return 0


def detect(value: int, enabled: bool) -> int:
    if enabled:
        return value + 1
    return 0
""",
    ),
    Fixture(
        "shadow-str-rejects-none",
        """import html
import re
import builtins
import typing as _typing


class Payload:
    def __str__(self) -> builtins.str:
        return "<&>"


def escape(
    value: Payload | None,
    quote: bool = True,
    *,
    str: _typing.Callable[[Payload], builtins.str] = builtins.str
) -> builtins.str:
    if value is not None:
        return html.escape(str(value), quote=quote)
    return ""


def detect(text: builtins.str) -> bool:
    if re.search("^yes", text, re.MULTILINE):
        return True
    return False
""",
    ),
)

OBSERVER = """import gc, importlib.util, json, re, sys

spec = importlib.util.spec_from_file_location("subject", sys.argv[1])
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
KIND = sys.argv[2]
MODE = ""
EVENTS = []
NEXT = 0


class Text(str):
    def __new__(cls, value):
        global NEXT
        result = super().__new__(cls, value)
        result.serial = NEXT
        NEXT += 1
        EVENTS.append(("new-text", result.serial, value))
        return result

    def replace(self, old, new, count=-1):
        EVENTS.append(("replace", self.serial, old, new))
        if MODE == "escape-error":
            raise ArithmeticError("escape")
        return Text(super().replace(old, new, count))

    def __del__(self):
        EVENTS.append(("drop-text", self.serial))


class Payload:
    def __init__(self):
        EVENTS.append(("new-payload",))

    def __str__(self):
        EVENTS.append(("str",))
        if MODE == "str-error":
            raise ArithmeticError("str")
        return Text("<&>")

    def __repr__(self):
        EVENTS.append(("repr",))
        if MODE == "repr-error":
            raise ArithmeticError("repr")
        return Text("<Payload>")

    def __del__(self):
        EVENTS.append(("drop-payload",))


class Condition:
    def __init__(self, value):
        self.value = value
        EVENTS.append(("new-condition", value))

    def __bool__(self):
        EVENTS.append(("truth", self.value))
        if MODE == "truth-error":
            raise ArithmeticError("truth")
        return self.value

    def __del__(self):
        EVENTS.append(("drop-condition",))


original_search = re.search


def search(pattern, text, flags):
    EVENTS.append(("search", text))
    if MODE == "search-error":
        raise ArithmeticError("search")
    return Condition(original_search(pattern, text, flags) is not None)


subject.re.search = search
for MODE in ("ok", "str-error", "repr-error", "escape-error"):
    for absent in (False, True):
        for quote in (False, True):
            EVENTS.clear()
            NEXT = 0
            try:
                if KIND == "text":
                    result = subject.escape(Text("<&>"), quote, enabled=not absent)
                else:
                    result = subject.escape(None if absent else Payload(), quote)
                EVENTS.append(("answer", repr(result)))
                del result
            except Exception as error:
                EVENTS.append(("exception", type(error).__name__, str(error)))
            gc.collect()
            print(json.dumps(["escape", MODE, absent, quote, EVENTS]))
for MODE in ("ok", "search-error", "truth-error"):
    for text in ("yes", "no"):
        EVENTS.clear()
        try:
            EVENTS.append(("answer", subject.detect(text)))
        except Exception as error:
            EVENTS.append(("exception", type(error).__name__, str(error)))
        gc.collect()
        print(json.dumps(["detect", MODE, text, EVENTS]))
"""


def _write_project(root: Path, fixture: Fixture) -> Path:
    root.mkdir()
    path = root / "subject.py"
    path.write_text(fixture.source)
    (root / "pyproject.toml").write_text(
        '[project]\nname="optional-converter"\nversion="0"\nrequires-python=">=3.11"\n'
        "[tool.mypy]\nstrict=true\n"
    )
    return path


def _observe(program: Path, observer: Path, fixture: Fixture) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, "-B", str(observer), str(program), fixture.input_kind],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"},
        timeout=30,
    )
    assert not result.stderr
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize("fixture", POSITIVE_FIXTURES, ids=lambda fixture: fixture.name)
def test_optional_aware_converter_stays_useful_and_precisely_generic(
    tmp_path: Path, fixture: Fixture
) -> None:
    path = _write_project(tmp_path / "source", fixture)
    checker = CountingMypy()
    try:
        assert checker.check_project({str(path): fixture.source}) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        proposals = [
            proposal
            for proposal in engine.analyze_file(str(path))
            if "escape and detect" in proposal.description
        ]
        assert len(proposals) == 1
        written = engine.apply_refactoring(str(path), proposals[0])
        prospective = [
            source
            for sources in checker.checked
            for source in sources.values()
            if "def __extracted_func_" in source
        ]
        assert len(prospective) == 1
        assert checker.check_project({str(path): written}) == CheckSuccess()
    finally:
        checker.close()
    helper = next(
        node
        for node in ast.parse(written).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    arguments = [*helper.args.posonlyargs, *helper.args.args]
    assert len(arguments) == 3 and all(argument.annotation is not None for argument in arguments)
    assert helper.returns is not None
    assert "_TowelT" in ast.unparse(helper.returns)
    assert "Any" not in ast.unparse(helper)
    assert written != fixture.source
    assert path.read_text() == fixture.source
    rewritten = tmp_path / "after.py"
    rewritten.write_text(written)
    observer = tmp_path / "observe.py"
    observer.write_text(OBSERVER)
    assert _observe(path, observer, fixture) == _observe(rewritten, observer, fixture)


@pytest.mark.parametrize("fixture", NEGATIVE_FIXTURES, ids=lambda fixture: fixture.name)
def test_required_nonoptional_converter_cannot_be_hidden_by_any(
    tmp_path: Path, fixture: Fixture
) -> None:
    path = _write_project(tmp_path / "source", fixture)
    checker = CountingMypy()
    try:
        assert checker.check_project({str(path): fixture.source}) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        proposals = [
            proposal
            for proposal in engine.analyze_file(str(path))
            if "escape and detect" in proposal.description
        ]
        assert len(proposals) == 1
        if fixture.name == "int-required":
            with pytest.raises(UntypeableExtraction) as refused:
                engine.apply_refactoring(str(path), proposals[0])
            assert refused.value.reason is Untypeable.NARROWING_READ_IN_THUNK
            assert all(
                "Any" not in source for sources in checker.checked for source in sources.values()
            )
        else:
            with pytest.raises(TypeRejectedExtraction):
                engine.apply_refactoring(str(path), proposals[0])
    finally:
        checker.close()
    assert path.read_text() == fixture.source


@pytest.mark.parametrize("fixture", POSITIVE_FIXTURES, ids=lambda fixture: fixture.name)
def test_optional_converter_cli_preserves_callbacks_errors_and_finalizers(
    tmp_path: Path, fixture: Fixture
) -> None:
    original = _write_project(tmp_path / "source", fixture)
    output = tmp_path / "output"
    runtime = Path(towel.__file__).resolve().parents[1]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(runtime)!r});"
        "sys.argv=['towel',*sys.argv[1:]];"
        "runpy.run_module('towel.cli',run_name='__main__')"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            bootstrap,
            "dry",
            str(original.parent),
            str(output),
            "--no-interactive",
            "--no-format",
            "--min-lines",
            "2",
            "--progress",
            "none",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    transformed = output / "subject.py"
    written = transformed.read_text()
    assert written != fixture.source
    assert original.read_text() == fixture.source
    helper = next(
        node
        for node in ast.parse(written).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    assert "Any" not in ast.unparse(helper)
    assert helper.returns is not None and "_TowelT" in ast.unparse(helper.returns)
    checker = CountingMypy()
    try:
        assert checker.check_project({str(transformed): written}) == CheckSuccess()
    finally:
        checker.close()
    observer = tmp_path / "observe.py"
    observer.write_text(OBSERVER)
    before = _observe(original, observer, fixture)
    assert len(before[1].splitlines()) == 22
    assert _observe(transformed, observer, fixture) == before


REFINEMENT_FIXTURES = (
    Fixture(
        "rich-getter",
        """class Task:
    def __init__(self, total: float | None, completed: float, finished: bool) -> None:
        self.total = total
        self.completed = completed
        self.finished = finished


def describe_total(task: Task, completed: int) -> int:
    value = (
        int(task.total)
        if task.total is not None
        else completed
    )
    return value + 1


def describe_done(task: Task, fallback: int) -> int:
    value = (
        int(task.completed)
        if task.finished
        else fallback
    )
    return value + 1
""",
    ),
    Fixture(
        "local-alias",
        """class Task:
    def __init__(self, total: float | None, completed: float, finished: bool) -> None:
        self.total = total
        self.completed = completed
        self.finished = finished

def describe_total(task: Task, completed: int) -> int:
    if task.total is not None:
        value = task.total
        answer = int(value)
    else:
        answer = completed
    return answer + 1

def describe_done(task: Task, fallback: int) -> int:
    if task.finished:
        value = task.completed
        answer = int(value)
    else:
        answer = fallback
    return answer + 1
""",
    ),
)


class CountingPyright(PyrightOracle):
    """Retain prospective sources so a refusal cannot conceal an Any rung."""

    def __init__(self) -> None:
        super().__init__()
        self.checked: list[Mapping[str, str]] = []

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checked.append(dict(sources))
        return super().check_project(sources, excluded_paths=excluded_paths)


@pytest.mark.parametrize("fixture", REFINEMENT_FIXTURES, ids=lambda fixture: fixture.name)
@pytest.mark.parametrize(
    "backend",
    [
        "mypy",
        pytest.param(
            "pyright",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("pyright") is None, reason="pyright absent"
            ),
        ),
    ],
)
def test_both_checkers_refuse_required_nullable_refinement_before_any(
    tmp_path: Path, fixture: Fixture, backend: str
) -> None:
    path = _write_project(tmp_path / "source", fixture)
    if backend == "pyright":
        (path.parent / "pyproject.toml").write_text(
            '[tool.pyright]\ntypeCheckingMode="strict"\nstrict=[]\n'
        )
    checker = CountingMypy() if backend == "mypy" else CountingPyright()
    try:
        assert checker.check_project({str(path): fixture.source}) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        proposals = [
            proposal
            for proposal in engine.analyze_file(str(path))
            if "describe_total and describe_done" in proposal.description
        ]
        assert len(proposals) == 1
        with pytest.raises(UntypeableExtraction) as refused:
            engine.apply_refactoring(str(path), proposals[0])
        assert refused.value.reason is Untypeable.NARROWING_READ_IN_THUNK
        assert "task.total" in refused.value.detail
        helpers = [
            source
            for sources in checker.checked
            for source in sources.values()
            if "def __extracted_func_" in source
        ]
        assert len(helpers) == (1 if backend == "mypy" else 2)
        assert all("Any" not in source for source in helpers)
    finally:
        checker.close()
    assert path.read_text() == fixture.source
