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

"""Every inner candidate check sees the current checker and project interfaces."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeDiagnostic,
    checks_in_turn,
)
from towel.unification.exceptions import TypeRejectedExtraction
from towel.unification.refactor_engine import UnificationRefactorEngine

_STRICT = '[tool.mypy]\nstrict = true\nfiles = ["subject.py"]\n'
_BASELINE = "def first() -> int:\n    return 1\n"
_VARIANT = (
    "from typing import Any\n"
    "def helper() -> Any:\n"
    "    return 1\n"
    "def first() -> int:\n"
    "    return helper()\n"
)


class _TransientPolicy:
    """Actual mypy verification plus a custom policy whose refusal can clear."""

    def __init__(self, inner: MypyInferrer) -> None:
        self.inner = inner
        self.refuse = True
        self.candidate_checks = 0

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return self.inner.reveal(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return self.inner.is_subtype(file_path, source, pairs)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        result = self.inner.check_project(sources, excluded_paths=excluded_paths)
        if isinstance(result, CheckFailure):
            return result
        for path, source in sources.items():
            helper = next(
                (
                    node
                    for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
                ),
                None,
            )
            if helper is not None:
                self.candidate_checks += 1
                if self.refuse:
                    return CheckSuccess(
                        (
                            *result.errors,
                            TypeDiagnostic(path, "Transient policy refusal", helper.body[0].lineno),
                        )
                    )
        return result

    def close(self) -> None:
        self.inner.close()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "subject.py"
    path.write_text(_BASELINE)
    config = tmp_path / "pyproject.toml"
    config.write_text(_STRICT)
    return path, config


def test_public_same_run_retry_hears_a_custom_oracle_again(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path, _ = _project(tmp_path)
    original = (
        "events: list[str] = []\n"
        "def first(value: int) -> int:\n"
        '    events.append("head")\n'
        '    events.append("tail")\n'
        "    return value + 1\n"
        "def second(value: int) -> int:\n"
        '    events.append("head")\n'
        '    events.append("tail")\n'
        "    return value + 2\n"
    )
    path.write_text(original)
    oracle = _TransientPolicy(MypyInferrer(tmp_path / "cache"))
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle, min_lines=3)
        proposal = engine.analyze_file(str(path))[0]
        engine.begin_refactoring_run([str(path)])
        with pytest.raises(TypeRejectedExtraction):
            engine.apply_refactoring_multi_file(proposal)
        refused_checks = oracle.candidate_checks
        assert refused_checks > 0
        oracle.refuse = False
        rendered = engine.apply_refactoring_multi_file(proposal)
        assert oracle.candidate_checks > refused_checks
        namespace: dict[str, object] = {}
        exec(compile(rendered[str(path)], str(path), "exec"), namespace)
        first, second = namespace["first"], namespace["second"]
        assert callable(first) and callable(second)
        assert [first(3), second(3)] == [4, 5]
        assert namespace["events"] == ["head", "tail", "head", "tail"]
        assert path.read_text() == original
    finally:
        oracle.close()


def test_every_builtin_attempt_checks_current_configuration_and_never_reuses_a_result(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mypy")
    path, config = _project(tmp_path)
    oracle = MypyInferrer(tmp_path / "cache")
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle)
        engine.begin_refactoring_run([str(path)])
        with patch(
            "towel.unification.annotation_wiring.checks_in_turn", wraps=checks_in_turn
        ) as checking:
            refused = engine._project_errors({str(path): _VARIANT})
            assert len(refused) == 1 and "Returning Any" in refused[0].message
            assert engine._project_errors({str(path): _VARIANT}) == refused
            assert checking.call_count == 2, "even an identical refusal must reach the checker"
            config.write_text(_STRICT + "warn_return_any = false\n")
            assert engine._project_errors({str(path): _VARIANT}) == ()
            assert checking.call_count == 3, "changed checker semantics require a check"
            assert engine._project_errors({str(path): _VARIANT}) == ()
            assert checking.call_count == 4, "accepted variants must not be memoized"
    finally:
        oracle.close()


def test_configured_mypy_plugin_refusals_are_not_retained(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path, config = _project(tmp_path)
    (tmp_path / "policy_plugin.py").write_text(
        "from mypy.plugin import Plugin\n"
        "class PolicyPlugin(Plugin):\n"
        "    pass\n"
        "def plugin(version: str) -> type[Plugin]:\n"
        "    return PolicyPlugin\n"
    )
    config.write_text(_STRICT + 'plugins = ["policy_plugin.py"]\n')
    oracle = MypyInferrer(tmp_path / "cache")
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle)
        engine.begin_refactoring_run([str(path)])
        with patch(
            "towel.unification.annotation_wiring.checks_in_turn", wraps=checks_in_turn
        ) as checking:
            assert engine._project_errors({str(path): _VARIANT})
            assert engine._project_errors({str(path): _VARIANT})
            assert checking.call_count == 2
    finally:
        oracle.close()


def test_workflow_context_changes_recheck_an_identical_variant(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path, _ = _project(tmp_path)
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    workflow = workflows / "typing.yml"
    workflow.write_text("jobs:\n  typing:\n    steps:\n      - run: mypy --strict subject.py\n")
    oracle = MypyInferrer(tmp_path / "cache")
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle)
        engine.begin_refactoring_run([str(path)])
        with patch(
            "towel.unification.annotation_wiring.checks_in_turn", wraps=checks_in_turn
        ) as checking:
            assert engine._project_errors({str(path): _VARIANT})
            workflow.write_text(workflow.read_text() + "# Current CI context changed\n")
            assert engine._project_errors({str(path): _VARIANT})
            assert checking.call_count == 2
    finally:
        oracle.close()


@pytest.mark.parametrize(
    "provider",
    ["existing_module", "new_module", "existing_package", "new_package", "configured_stub_root"],
)
def test_changed_or_new_stubs_are_seen_by_the_next_candidate_check(
    tmp_path: Path, provider: str
) -> None:
    pytest.importorskip("mypy")
    path, config = _project(tmp_path)
    original = "from api import choose\ndef first() -> int:\n    return choose(1)\n"
    variant = (
        "from api import choose\n"
        "def helper() -> int:\n"
        '    return choose("allowed_after_stub_change")\n'
        "def first() -> int:\n"
        "    return helper()\n"
    )
    path.write_text(original)
    directory = tmp_path
    name = "api"
    if "package" in provider:
        directory = tmp_path / "api"
        directory.mkdir()
        name = "__init__"
    if provider == "configured_stub_root":
        directory = tmp_path / "stubs"
        directory.mkdir()
        config.write_text(_STRICT + 'mypy_path = "stubs"\n')
    stub = directory / f"{name}.pyi"
    if provider.startswith("new_"):
        (directory / f"{name}.py").write_text("def choose(value: int) -> int:\n    return value\n")
        assert not stub.exists()
    else:
        stub.write_text("def choose(value: int) -> int: ...\n")
    oracle = MypyInferrer(tmp_path / "cache")
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle)
        engine.begin_refactoring_run([str(path)])
        with patch(
            "towel.unification.annotation_wiring.checks_in_turn", wraps=checks_in_turn
        ) as checking:
            rejected = engine._project_errors({str(path): variant})
            assert len(rejected) == 1 and "incompatible type" in rejected[0].message
            stub.write_text("def choose(value: int | str) -> int: ...\n")
            assert engine._project_errors({str(path): variant}) == ()
            assert checking.call_count == 2, "a type interface can change without a .py change"
        assert path.read_text() == original
    finally:
        oracle.close()
