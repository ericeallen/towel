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

"""Every mypy build must judge supplied source instead of the physical baseline."""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer, RevealRequest, Subtyping
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

INITIALIZERS = """\
class Literal:
    def __init__(self, match_string: str) -> None:
        self.match = match_string
        self.matchLen = len(match_string)

class Keyword:
    def __init__(self, match_string: str) -> None:
        self.match = match_string
        self.matchLen = len(match_string)
"""
EXTRACTED = """\
from typing import Any

def extracted_attrs(value: Any, match_string: str, /) -> None:
    value.match = match_string
    value.matchLen = len(match_string)

class Literal:
    def __init__(self, match_string: str) -> None:
        extracted_attrs(self, match_string)

class Keyword:
    def __init__(self, match_string: str) -> None:
        extracted_attrs(self, match_string)
"""
CONSUMER = """\
def consumer(value: Literal | Keyword) -> tuple[str, int]:
    return value.match, value.matchLen
"""


@pytest.mark.parametrize("cross_file", [False, True])
@pytest.mark.parametrize("sparse", [False, True])
@pytest.mark.parametrize("per_module", [False, True])
def test_warm_and_fresh_candidates_preserve_constructor_attribute_diagnostics(
    tmp_path: Path, cross_file: bool, sparse: bool, per_module: bool
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    provider = package / "provider.py"
    original = INITIALIZERS + ("\n" + CONSUMER if not cross_file else "")
    changed = EXTRACTED + ("\n" + CONSUMER if not cross_file else "")
    provider.write_text(original)
    if cross_file:
        (package / "consumer.py").write_text(
            "from .provider import Literal, Keyword\n\n" + CONSUMER
        )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nfiles = ["pkg"]\n'
        + (
            '[[tool.mypy.overrides]]\nmodule = ["pkg.*"]\ndisallow_untyped_defs = true\n'
            if per_module
            else ""
        )
    )
    complete = {str(path): path.read_text() for path in package.glob("*.py")}
    candidate = {str(provider): changed} if sparse else {**complete, str(provider): changed}
    checker = MypyInferrer()
    try:
        checker.begin_run(list(complete))
        baseline = checker.check_project(complete)
        assert isinstance(baseline, CheckSuccess) and not baseline.errors
        expected_errors: Counter[tuple[str, str]] = Counter()
        for cold in (False, True):
            if cold:
                checker.forget_warm_state()
            prospective = checker.check_project(candidate)
            assert isinstance(prospective, CheckSuccess)
            assert len(prospective.errors) == 4
            assert all('has no attribute "match' in error.message for error in prospective.errors)
            expected_errors = Counter((error.path, error.message) for error in prospective.errors)
            assert provider.read_text() == original
        provider.write_text(changed)
        written = checker.check_project(candidate)
        assert isinstance(written, CheckSuccess)
        assert Counter((error.path, error.message) for error in written.errors) == expected_errors
        provider.write_text(original)
        # No reset: an overlay cache must also answer correctly when restored.
        restored = checker.check_project(complete)
        assert isinstance(restored, CheckSuccess) and not restored.errors
    finally:
        checker.close()


def test_foreign_second_build_honors_text_and_per_module_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mypy import build
    from mypy.build import BuildSource
    from mypy.options import Options
    from typing import Callable, Sequence

    from towel import _mypy_worker as worker

    monkeypatch.chdir(tmp_path)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    provider = package / "provider.py"
    provider.write_text(INITIALIZERS)
    consumer = package / "consumer.py"
    consumer_text = "from .provider import Literal, Keyword\n\n" + CONSUMER
    consumer.write_text(consumer_text)
    config = tmp_path / "pyproject.toml"
    config.write_text(
        '[tool.mypy]\nfiles = ["pkg/consumer.py"]\nignore_errors = true\n'
        '[[tool.mypy.overrides]]\nmodule = ["pkg.consumer"]\nignore_errors = false\n'
        '[[tool.mypy.overrides]]\nmodule = ["pkg.provider"]\ndisallow_untyped_defs = true\n'
    )
    original_build = worker._build_as_the_project_reaches
    supplied_providers: list[bool] = []
    parser_states: list[tuple[bool, bool, bool]] = []

    def recorded(
        sources: Sequence[BuildSource],
        options: Options,
        judged: Callable[[str], bool],
        *,
        complete: bool,
    ) -> tuple[build.BuildResult, list[BuildSource]]:
        parser_states.append(
            (
                bool(getattr(options, "native_parser", False)),
                bool(getattr(options.clone_for_module("pkg.provider"), "native_parser", False)),
                bool(getattr(options.clone_for_module("pkg.consumer"), "native_parser", False)),
            )
        )
        supplied_providers.append(
            any(source.path == str(provider) and source.text is not None for source in sources)
        )
        return original_build(sources, options, judged, complete=complete)

    monkeypatch.setattr(worker, "_build_as_the_project_reaches", recorded)
    cache = str(tmp_path / "cache")

    def checked(foreign: str) -> list[str]:
        return worker._request(
            {
                "root": str(tmp_path),
                "config": str(config),
                "complete": True,
                "sources": {str(consumer): consumer_text},
                "foreign": {str(provider): foreign},
                "modules": {},
                "excluded_paths": [],
            },
            cache,
        ).messages

    assert checked(INITIALIZERS) == []
    supplied_providers.clear()
    errors = checked(EXTRACTED)
    assert supplied_providers == [False, True]
    assert len(errors) == 4 and all('has no attribute "match' in error for error in errors)
    assert all(str(consumer) in error for error in errors)
    assert checked(INITIALIZERS) == []
    assert all(states == (False, False, False) for states in parser_states)
    assert provider.read_text() == INITIALIZERS and consumer.read_text() == consumer_text


def test_warm_reveal_and_subtype_probes_read_inserted_text(tmp_path: Path) -> None:
    module = tmp_path / "provider.py"
    original = "def identity(value: int) -> int:\n    return value\n"
    module.write_text(original)
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    checker = MypyInferrer()
    try:
        baseline = checker.check_project({str(module): original})
        assert isinstance(baseline, CheckSuccess) and not baseline.errors
        for cold in (False, True):
            if cold:
                checker.forget_warm_state()
            revealed = checker.reveal([RevealRequest(str(module), original, 2, "    ", ("value",))])
            assert revealed[(str(module), 2, 0)].removeprefix("builtins.") == "int"
            assert checker.is_subtype(
                str(module), original, [("bool", "int"), ("int", "bool")]
            ) == [
                Subtyping.YES,
                Subtyping.NO,
            ]
            assert module.read_text() == original
    finally:
        checker.close()


def test_constructor_fixture_keeps_useful_typed_extraction(tmp_path: Path) -> None:
    fixture = Path(__file__).with_name("hostile_cases") / "rmypyoverlay_constructor_fields.py"
    source = fixture.read_text()
    module = tmp_path / "m.py"
    module.write_text(source)
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    checker = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=checker)
        proposals = engine.analyze_files([str(module)], progress="none")
        assert proposals
        transformed = engine.apply_refactoring(str(module), proposals[0])
        assert transformed != source and "extracted_func" in transformed
        result = checker.check_project({str(module): transformed})
        assert isinstance(result, CheckSuccess) and not result.errors
        namespace: dict[str, object] = {}
        exec(
            compile(
                transformed
                + "\nassert consumer(Literal('x')) == ('x', 1)\nassert consumer(Keyword('yy')) == ('yy', 2)\nassert first(4) == 9\nassert second(4) == 14\n",
                str(module),
                "exec",
            ),
            namespace,
        )
        assert module.read_text() == source
    finally:
        checker.close()
