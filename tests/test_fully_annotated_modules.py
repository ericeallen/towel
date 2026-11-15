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

"""A fully annotated module never gets an unannotated helper, whatever the configured checker accepts.

idna configures no mypy, so Towel checks it with mypy's defaults, which skip
the bodies of unannotated functions. Both annotated variants of a helper were
refused -- its ``result = []`` needs an annotation -- and the unannotated one
was accepted, making it the only unannotated function of a module whose every
function is annotated. idna's CI runs ``mypy --strict idna``, which refused the
output with four errors. Where every function of the helper's module is
annotated, the ladder now ends there with a decline that says so; a module
that already holds unannotated functions keeps the rung.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.typed_fixtures import apply_one, requires_mypy
from towel.unification.argument_ownership import _supported_runtime
from towel.unification.refactor_engine import UnificationRefactorEngine

NO_MYPY_CONFIGURATION = "[project]\nname = 'labels'\nversion = '0'\n"

LABELS = """
def encode() -> list[str]:
    text = "a.b"
    strict = True
    trailing_dot = False
    result = []
    labels = text.split(".") if strict else text.split(",")
    if not labels or labels == [""]:
        raise ValueError("empty")
    result.append(labels[0].upper())
    return result if not trailing_dot else result + [""]


def decode() -> list[str]:
    text = "a.b"
    strict = True
    trailing_dot = False
    result = []
    labels = text.split(".") if strict else text.split(",")
    if not labels or labels == [""]:
        raise ValueError("empty")
    result.extend([label.lower() for label in labels])
    return result if not trailing_dot else result + [""]
"""


SINGLE_BINDING_LABELS = """
def encode() -> list[str]:
    result = []
    if not "a.b":
        raise ValueError("empty")
    result.append("a")
    return result


def decode() -> list[str]:
    result = []
    if not "a.b":
        raise ValueError("empty")
    result.extend(["b", "c"])
    return result
"""
"""The empty-list inference failure without an order among multiple helper locals."""


def _assert_uncertified_labels_prefix_refused(tmp_path: Path, source: str) -> None:
    path = tmp_path / "labels.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, skip_trivial_helpers=False
    )
    proposals = engine.analyze_file(str(path))
    assert "lifetime_order_uncertified" in engine.declined_pairs
    # Smaller safe prefixes remain discoverable, but the historical helper
    # that returns result alongside the other locals must never be proposed.
    assert all(
        not any(
            isinstance(node, ast.Name) and node.id == "result"
            for node in ast.walk(proposal.extracted_function)
        )
        for proposal in proposals
    )
    assert path.read_text() == source


@requires_mypy
@pytest.mark.parametrize("source", [LABELS, SINGLE_BINDING_LABELS], ids=["original", "one-local"])
def test_only_an_unannotated_helper_is_declined_in_a_fully_annotated_module(
    tmp_path: Path,
    source: str,
) -> None:
    if source == LABELS and not _supported_runtime():
        _assert_uncertified_labels_prefix_refused(tmp_path, source)
        return
    outcome = apply_one(tmp_path, source, pick="encode and decode", config=NO_MYPY_CONFIGURATION)
    assert outcome.error is not None, outcome.module
    message = str(outcome.error)
    assert "would be the one unannotated function of its module" in message, message
    assert outcome.checked_helpers and all(
        " -> " in signature for signature in outcome.checked_helpers
    ), outcome.checked_helpers


@requires_mypy
@pytest.mark.parametrize("source", [LABELS, SINGLE_BINDING_LABELS], ids=["original", "one-local"])
def test_a_module_that_already_has_unannotated_functions_keeps_the_rung(
    tmp_path: Path, source: str
) -> None:
    original = source + "\n\ndef legacy(value):\n    return value\n"
    if source == LABELS and not _supported_runtime():
        _assert_uncertified_labels_prefix_refused(tmp_path, original)
        return
    outcome = apply_one(
        tmp_path,
        original,
        pick="encode and decode",
        config=NO_MYPY_CONFIGURATION,
    )
    assert outcome.error is None, outcome.error
    helper = outcome.helper()
    assert helper.returns is None and all(a.annotation is None for a in helper.args.args)
