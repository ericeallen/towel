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

from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy

NO_MYPY_CONFIGURATION = "[project]\nname = 'labels'\nversion = '0'\n"

LABELS = """
def encode(text: str, strict: bool) -> list[str]:
    trailing_dot = False
    result = []
    labels = text.split(".") if strict else text.split(",")
    if not labels or labels == [""]:
        raise ValueError("empty")
    for label in labels:
        result.append(label.upper())
    return result if not trailing_dot else result + [""]


def decode(text: str, strict: bool) -> list[str]:
    trailing_dot = False
    result = []
    labels = text.split(".") if strict else text.split(",")
    if not labels or labels == [""]:
        raise ValueError("empty")
    for label in labels:
        result.append(label.lower())
    return result if not trailing_dot else result + [""]
"""


@requires_mypy
def test_only_an_unannotated_helper_is_declined_in_a_fully_annotated_module(
    tmp_path: Path,
) -> None:
    outcome = apply_one(tmp_path, LABELS, pick="encode and decode", config=NO_MYPY_CONFIGURATION)
    assert outcome.error is not None, outcome.module
    message = str(outcome.error)
    assert "would be the one unannotated function of its module" in message, message
    assert all(": " in signature for signature in outcome.checked_helpers), outcome.checked_helpers


@requires_mypy
def test_a_module_that_already_has_unannotated_functions_keeps_the_rung(tmp_path: Path) -> None:
    outcome = apply_one(
        tmp_path,
        LABELS + "\n\ndef legacy(value):\n    return value\n",
        pick="encode and decode",
        config=NO_MYPY_CONFIGURATION,
    )
    assert outcome.error is None, outcome.error
    helper = outcome.helper()
    assert helper.returns is None and all(a.annotation is None for a in helper.args.args)
