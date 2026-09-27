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

"""Batching must save checker exchanges without changing what a probe means.

Distinct Sphinx files caused distinct whole-project waits. A batch must retain
identical answers, excluded-file behavior, failed-project evidence and consumer
validation. Fewer calls alone does not satisfy these expectations.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Mapping

import pytest

from towel import type_inference
from towel.pyright_session import Diagnostic, SessionFailure
from towel.type_inference import (
    CheckFailure,
    CheckSuccess,
    PyrightOracle,
    RevealRequest,
    unanswered_files,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)


def _project(root: Path) -> tuple[Path, Path, Path]:
    package = root / "pkg"
    package.mkdir(parents=True)
    (root / "pyrightconfig.json").write_text(
        '{"typeCheckingMode":"strict","include":["pkg"],"ignore":["pkg/ignored.py"]}'
    )
    (package / "__init__.py").write_text("")
    provider, consumer, ignored = (
        package / name for name in ("provider.py", "consumer.py", "ignored.py")
    )
    provider.write_text("def make() -> int:\n    return 1\n")
    consumer.write_text(
        "from pkg.provider import make\ndef use() -> int:\n    value: int = make()\n    return value\n"
    )
    ignored.write_text("def unused() -> str:\n    return 'ignored'\n")
    return provider, consumer, ignored


def _requests(paths: tuple[Path, Path, Path]) -> list[RevealRequest]:
    provider, consumer, ignored = paths
    return [
        RevealRequest(str(provider), provider.read_text(), 2, "    ", ("1",)),
        RevealRequest(str(consumer), consumer.read_text(), 3, "    ", ("make()", "'label'")),
        RevealRequest(str(ignored), ignored.read_text(), 2, "    ", ("'ignored'",)),
    ]


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_batch_answers_match_individual_probes_and_follow_dependency_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language_server: bool
) -> None:
    paths = _project(tmp_path)
    requests = _requests(paths)
    original = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    separate = PyrightOracle(language_server=language_server)
    try:
        expected = {
            key: value for request in requests for key, value in separate.reveal([request]).items()
        }
    finally:
        separate.close()
    oracle = PyrightOracle(language_server=language_server)
    exchanges: list[tuple[Path, tuple[str, ...]]] = []
    diagnostics = oracle._probe_diagnostics

    def record(
        root: Path, sources: Mapping[str, str]
    ) -> Mapping[str, type_inference._PyrightDiagnostics | CheckFailure]:
        exchanges.append((root, tuple(sources)))
        return diagnostics(root, sources)

    monkeypatch.setattr(oracle, "_probe_diagnostics", record)
    try:
        actual = oracle.reveal(requests)
        assert actual == expected
        assert len(actual) == 3 and not unanswered_files(actual)
        assert exchanges == [(tmp_path, tuple(str(path) for path in paths[:2]))]
        if language_server:
            assert oracle.answered_from_a_session, "the supposed warm test fell back"
        assert {path: path.read_bytes() for path in original} == original
        assert set(original) == {path for path in tmp_path.rglob("*") if path.is_file()}
        # A fresh disk revision must invalidate the warm verdict. The batch
        # probes keep their original provider text while the consumer is read
        # from the same candidate revision, not from an installed copy.
        provider, consumer, _ = paths
        provider.write_text("def make() -> str:\n    return 'changed'\n")
        changed = oracle.reveal(_requests(paths))
        assert changed[(str(consumer), 3, 0)] == "str"
        checked = oracle.check_project({str(provider): provider.read_text()})
        assert isinstance(checked, CheckSuccess)
        assert any(error.path == str(consumer) for error in checked.errors), checked
    finally:
        oracle.close()


def test_a_failed_batch_marks_every_affected_file_but_keeps_other_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _project(tmp_path / "bad")
    good = _project(tmp_path / "good")
    oracle = PyrightOracle(language_server=False)
    real = oracle._run_diagnostics

    def fail_one(
        root: Path, project: Path, paths: tuple[str, ...] = ()
    ) -> type_inference._PyrightDiagnostics | CheckFailure:
        if root == tmp_path / "bad":
            return CheckFailure("injected checker failure")
        return real(root, project, paths)

    monkeypatch.setattr(oracle, "_run_diagnostics", fail_one)
    try:
        actual = oracle.reveal(_requests(bad) + _requests(good))
    finally:
        oracle.close()
    assert unanswered_files(actual) == {str(path): "injected checker failure" for path in bad[:2]}
    assert set(key[0] for key in actual) == {str(path) for path in good[:2]}


def test_server_failure_retries_the_complete_batch_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _project(tmp_path)
    oracle = PyrightOracle()

    def fail(
        self: type_inference._WarmProject, replacements: Mapping[str, str]
    ) -> dict[str, list[Diagnostic]]:
        raise SessionFailure("injected session failure")

    monkeypatch.setattr(type_inference._WarmProject, "diagnostics", fail)
    try:
        actual = oracle.reveal(_requests(paths))
        assert oracle.answered_from_a_session and not oracle._warmed
        assert not unanswered_files(actual)
        assert set(key[0] for key in actual) == {str(path) for path in paths[:2]}
        assert actual[(str(paths[1]), 3, 0)] == "int"
    finally:
        oracle.close()
