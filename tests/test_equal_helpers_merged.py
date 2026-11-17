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

"""Audit R9 / shape 6: equal generated bodies share one implementation."""

import ast
from pathlib import Path

import pytest

from towel.unification.helper_merging import merge_module_helpers
from towel.unification.refactor_engine import UnificationRefactorEngine

SOURCE = (
    "def __extracted_func_0(value):\n"
    "    result = value * 2\n"
    "    return result + 1\n\n"
    "def __extracted_func_1(value):\n"
    "    total = value * 2\n"
    "    return total + 1\n\n"
    "answer = (__extracted_func_0(3), __extracted_func_1(value=4))\n"
)


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("strict", [False, True])
def test_finished_run_merges_equal_helpers_before_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, strict: bool
) -> None:
    """R9: cleanup also runs when the analysis found no further extractions."""
    root = tmp_path / "input"
    root.mkdir()
    path = root / "sample.py"
    path.write_text(SOURCE)
    engine = UnificationRefactorEngine(strict=strict)
    # Isolate cleanup from extraction: the final pass sees a completed module.
    monkeypatch.setattr(engine, "analyze_files", lambda *args, **kwargs: [])
    confirmed: list[str] = []
    monkeypatch.setattr(
        engine,
        "confirm_run_with_a_cold_checker",
        lambda paths: confirmed.extend(Path(item).read_text() for item in paths),
    )
    if directory:
        engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
        output = path.read_text()
    else:
        output, count, _ = engine.refactor_to_fixed_point(str(path), progress="none")
        assert count == 1
    helpers = [node for node in ast.parse(output).body if isinstance(node, ast.FunctionDef)]
    assert len(helpers) == 1
    assert "__extracted_func_1 = __extracted_func_0" in output
    assert confirmed == [output]
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(SOURCE, before)
    exec(output, after)
    assert before["answer"] == after["answer"] == (7, 9)


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace("__extracted_func_", "user_function_"),
        SOURCE.replace("def __extracted_func_", "@decorate\ndef __extracted_func_"),
        SOURCE.replace("(value)", "(value=make_default())"),
        SOURCE.replace("(value)", "(value: make_annotation())"),
        SOURCE.replace("total + 1", "total + 2"),
        SOURCE.replace("    total =", "    global total\n    total ="),
        SOURCE.replace("    return result + 1", "    return locals()").replace(
            "    return total + 1", "    return locals()"
        ),
        SOURCE.replace(
            "def __extracted_func_1(value):", "def __extracted_func_1(value):  # towel: no-extract"
        ),
        SOURCE + "__extracted_func_0 = replacement\n",
    ],
)
def test_unequal_or_observable_definitions_are_not_merged(source: str) -> None:
    """R9: merging must retain user functions, headers, protected code and bindings."""
    merged = merge_module_helpers("/sample.py", source, {"/sample.py": source})
    assert merged.source == source
    assert merged.aliases == ()


def test_program_attribute_rebinding_prevents_alias() -> None:
    """R9: a call between definitions must not change the aliased target."""
    program = {
        "/sample.py": SOURCE,
        "/consumer.py": "import sample\nsample.__extracted_func_0 = other\n",
    }
    assert merge_module_helpers("/sample.py", SOURCE, program).source == SOURCE


def test_comments_and_keyword_signature_survive() -> None:
    """R9: retained source is untouched and keyword argument names stay valid."""
    source = SOURCE.replace("    result =", "    # explanation\n    result =")
    merged = merge_module_helpers("/sample.py", source, {"/sample.py": source})
    assert "    # explanation\n    result = value * 2" in merged.source
    assert merged.aliases == (("__extracted_func_1", "__extracted_func_0"),)
    assert (
        merge_module_helpers("/sample.py", merged.source, {"/sample.py": merged.source}).source
        == merged.source
    )


@pytest.mark.parametrize("escape", [False, True])
def test_positional_parameter_names_may_differ_only_without_keyword_consumers(escape: bool) -> None:
    """R9: Sopel's add_attribute/function and actual_decorator/message share a body."""
    source = (
        "def __extracted_func_0(add_attribute, function):\n"
        "    if callable(function):\n"
        "        return add_attribute(function)\n"
        "    return add_attribute\n\n"
        "def __extracted_func_1(actual_decorator, message):\n"
        "    if callable(message):\n"
        "        return actual_decorator(message)\n"
        "    return actual_decorator\n\n"
        + ("saved = __extracted_func_1\n" if escape else "answer = __extracted_func_1(str, 2)\n")
    )
    result = merge_module_helpers("/sample.py", source, {"/sample.py": source})
    assert len(result.aliases) == (0 if escape else 1)
    if not escape:
        assert "__extracted_func_1 = __extracted_func_0" in result.source
    keyword_source = source + "answer = __extracted_func_1(actual_decorator=str, message=2)\n"
    assert not merge_module_helpers(
        "/sample.py", keyword_source, {"/sample.py": keyword_source}
    ).aliases


def test_parameter_names_observed_by_locals_are_not_alpha_equivalent() -> None:
    """R9: parameter alpha conversion cannot change keys returned by locals()."""
    source = (
        "def __extracted_func_0(first):\n    return locals()\n"
        "def __extracted_func_1(second):\n    return locals()\n"
        "answer = __extracted_func_1(1)\n"
    )
    assert not merge_module_helpers("/sample.py", source, {"/sample.py": source}).aliases


@pytest.mark.parametrize("frame", ["sys._getframe()", "inspect.currentframe()"])
def test_frame_local_names_are_not_alpha_equivalent(frame: str) -> None:
    """R9: frame-local mappings expose ordinary local names just as locals() does."""
    source = (
        "import sys\nimport inspect\n"
        "def __extracted_func_0(value):\n"
        "    first = value * 2\n"
        f"    return {frame}.f_locals.copy()\n"
        "def __extracted_func_1(value):\n"
        "    second = value * 2\n"
        f"    return {frame}.f_locals.copy()\n"
        "answer = __extracted_func_1(3)\n"
    )
    merged = merge_module_helpers("/sample.py", source, {"/sample.py": source})
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(source, before)
    exec(merged.source, after)
    assert before["answer"] == after["answer"] == {"value": 3, "second": 6}
    assert not merged.aliases


@pytest.mark.parametrize("directory", [False, True])
def test_cleanup_respects_the_remaining_iteration_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    """R9: merging is a refactoring and must obey the run's iteration cap."""
    root = tmp_path / "input"
    root.mkdir()
    path = root / "sample.py"
    source = (
        SOURCE + "def __extracted_func_2(value):\n    final = value * 2\n    return final + 1\n"
    )
    path.write_text(source)
    engine = UnificationRefactorEngine()
    monkeypatch.setattr(engine, "analyze_files", lambda *args, **kwargs: [])
    if directory:
        results, termination = engine.refactor_directory_to_fixed_point(
            str(root), str(root), max_iterations=1, progress="none"
        )
        assert sum(count for count, _ in results.values()) == 1
        assert termination == "iteration_cap"
        output = path.read_text()
    else:
        output, count, _ = engine.refactor_to_fixed_point(
            str(path), max_iterations=1, progress="none"
        )
        assert count == 1
    assert sum(isinstance(node, ast.FunctionDef) for node in ast.parse(output).body) == 2


def test_unreadable_consumer_keeps_equal_helpers_separate() -> None:
    """R9: an unreadable consumer cannot establish absence of rebinding."""
    assert not merge_module_helpers(
        "/sample.py", SOURCE, {"/sample.py": SOURCE, "/consumer.py": None}
    ).aliases
