# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Repeated builtin-mapping guards preserve diagnostics while avoiding repeated lexing."""

from __future__ import annotations

import ast
import gc
import sys
import warnings
import weakref

import pytest

from towel import parsing_warnings
from towel.unification import semantic_safety as safety
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.module_bindings import ModuleBindings, global_bindings
from towel.unification.scope_analyzer import ScopeAnalyzer

FUNCTION = "def sample(data):\n    return len(data)\n"


def _analyzed(source: str) -> tuple[ScopeAnalyzer, ast.FunctionDef]:
    module = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "sample"
    )
    return analyzer, function


@pytest.fixture
def module_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Observe real source analysis rather than replacing its result."""
    original = global_bindings
    seen: list[str] = []

    def observed(source: str) -> ModuleBindings | None:
        seen.append(source)
        return original(source)

    monkeypatch.setattr(safety, "global_bindings", observed)
    return seen


@pytest.mark.parametrize("replaces", [False, True])
def test_large_raw_regex_module_is_not_retokenized_for_each_builtin_guard(
    monkeypatch: pytest.MonkeyPatch, replaces: bool
) -> None:
    # More-itertools' 174,732-character module exceeded the global lexical
    # admission cap. Repeated pair guards then tokenized it thousands of times.
    source = (
        "# "
        + "padding" * 20000
        + "\n"
        + r"pattern = r'\d'"
        + "\n"
        + ("__builtins__ = {}\n" if replaces else "")
        + FUNCTION
    )
    assert len(source) > parsing_warnings._MAX_SOURCE_CHARACTERS
    analyzer, function = _analyzed(source)
    original = parsing_warnings._tokens_cannot_warn
    scanned: list[str] = []

    def observed(text: str) -> bool:
        scanned.append(text)
        return original(text)

    monkeypatch.setattr(parsing_warnings, "_tokens_cannot_warn", observed)
    expected = frozenset({"len"}) if replaces else frozenset()
    assert safety.builtin_mapping_reads(function, function.body, analyzer, source) == expected
    warm_scans = len(scanned)
    assert warm_scans > 0
    for _ in range(4):
        equal_text = source.encode().decode()
        assert equal_text == source and equal_text is not source
        assert (
            safety.builtin_mapping_reads(function, function.body, analyzer, equal_text) == expected
        )
    assert len(scanned) == warm_scans


def test_changed_source_and_reanalyzed_owner_cannot_reuse_an_old_binding_answer() -> None:
    analyzer, function = _analyzed(FUNCTION)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, FUNCTION) == frozenset()
    changed = "__builtins__ = {}\n" + FUNCTION
    module = ast.parse(changed)
    analyzer.analyze(module)
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef))
    assert safety.builtin_mapping_reads(function, function.body, analyzer, changed) == frozenset(
        {"len"}
    )
    module = ast.parse(FUNCTION)
    analyzer.analyze(module)
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef))
    assert safety.builtin_mapping_reads(function, function.body, analyzer, FUNCTION) == frozenset()


def test_attribute_write_in_changed_source_invalidates_a_safe_source_answer() -> None:
    analyzer, function = _analyzed(FUNCTION)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, FUNCTION) == frozenset()
    # The source predicate is independent of the analyzer's AST. An ordinary
    # alias need not be known to denote this module for it to invalidate proof.
    changed = "def configure(target):\n    target.__builtins__ = {}\n" + FUNCTION
    assert safety.builtin_mapping_reads(function, function.body, analyzer, changed) == frozenset(
        {"len"}
    )
    assert safety.builtin_mapping_reads(function, function.body, analyzer, FUNCTION) == frozenset()


def test_project_assignment_fact_is_separate_from_the_pure_source_memo(
    module_reads: list[str],
) -> None:
    analyzer, function = _analyzed(FUNCTION)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, FUNCTION) == frozenset()
    assert safety.builtin_mapping_reads(
        function, function.body, analyzer, FUNCTION, project_may_replace=True
    ) == frozenset({"len"})
    assert safety.builtin_mapping_reads(
        function, [], analyzer, FUNCTION, project_may_replace=True
    ) == (frozenset())
    assert safety.builtin_mapping_reads(function, function.body, analyzer, FUNCTION) == frozenset()
    assert len(module_reads) == 1


@pytest.mark.parametrize(
    "prefix",
    [
        "target.__builtins__ = {}\n",
        "del target.__builtins__\n",
        "target.__builtins__ += replacement\n",
        "for target.__builtins__ in mappings:\n    pass\n",
        "with provider as target.__builtins__:\n    pass\n",
        "[None for target.__builtins__ in mappings]\n",
    ],
)
def test_source_attribute_write_predicate_covers_ordinary_assignment_targets(prefix: str) -> None:
    source = prefix + FUNCTION
    analyzer, function = _analyzed(source)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, source) == frozenset(
        {"len"}
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "value = target.__builtins__\n",
        "target.__builtins__['len'] = replacement\n",
        "target.other = replacement\n",
        "setattr(target, '__builtins__', replacement)\n",
    ],
)
def test_source_attribute_fact_does_not_confuse_reads_mutation_or_reflection(prefix: str) -> None:
    source = prefix + FUNCTION
    analyzer, function = _analyzed(source)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, source) == frozenset()


def test_cached_module_fact_keeps_each_blocks_lexical_reads_fresh(module_reads: list[str]) -> None:
    source = "__builtins__ = {}\n" + FUNCTION + ("def shadowed(len, data):\n    return len(data)\n")
    analyzer, function = _analyzed(source)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, source) == frozenset(
        {"len"}
    )
    assert safety.builtin_mapping_reads(function, [], analyzer, source) == frozenset()
    module = analyzer.analyzed_tree
    assert isinstance(module, ast.Module)
    shadowed = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "shadowed"
    )
    assert safety.builtin_mapping_reads(shadowed, shadowed.body, analyzer, source) == frozenset()
    assert len(module_reads) == 1


def test_missing_source_keeps_unparsing_the_current_analyzed_tree() -> None:
    analyzer, function = _analyzed(FUNCTION)
    assert safety.builtin_mapping_reads(function, function.body, analyzer, "") == frozenset()
    module = analyzer.analyzed_tree
    assert isinstance(module, ast.Module)
    module.body.insert(0, ast.parse("__builtins__ = {}\n").body[0])
    assert safety.builtin_mapping_reads(function, function.body, analyzer, "") == frozenset({"len"})


def test_parser_digit_limit_changes_invalidate_the_source_answer() -> None:
    previous = sys.get_int_max_str_digits()
    source = "number = " + "1" * 1000 + "\n" + FUNCTION
    try:
        sys.set_int_max_str_digits(0)
        analyzer, function = _analyzed(source)
        assert (
            safety.builtin_mapping_reads(function, function.body, analyzer, source) == frozenset()
        )
        # A parser failure is unknown, so the mapping guard must decline.
        sys.set_int_max_str_digits(640)
        assert safety.builtin_mapping_reads(function, function.body, analyzer, source) == frozenset(
            {"len"}
        )
        sys.set_int_max_str_digits(0)
        assert (
            safety.builtin_mapping_reads(function, function.body, analyzer, source) == frozenset()
        )
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("project_may_replace", [False, True])
def test_warning_capable_sources_replay_native_warnings_and_respect_changed_filters(
    project_may_replace: bool,
) -> None:
    source = r"pattern = '\d'" + "\n" + FUNCTION
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        analyzer, function = _analyzed(source)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        for _ in range(2):
            assert safety.builtin_mapping_reads(
                function,
                function.body,
                analyzer,
                source,
                project_may_replace=project_may_replace,
            ) == (frozenset({"len"}) if project_may_replace else frozenset())
    assert len(emitted) == 2
    assert all(issubclass(item.category, (DeprecationWarning, SyntaxWarning)) for item in emitted)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert safety.builtin_mapping_reads(
            function, function.body, analyzer, source, project_may_replace=project_may_replace
        ) == frozenset({"len"})


def test_disabled_memoization_bypasses_warmed_answers_and_retains_no_new_answer(
    module_reads: list[str],
) -> None:
    analyzer, _ = _analyzed(FUNCTION)
    changed = "__builtins__ = {}\n" + FUNCTION
    assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
    with memoization_disabled():
        assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
        assert safety._module_may_replace_builtins(analyzer, changed)
    assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
    assert len(module_reads) == 3
    assert safety._module_may_replace_builtins(analyzer, changed)
    assert len(module_reads) == 4


class _HostileText(str):
    def __hash__(self) -> int:
        raise AssertionError("A source subclass must not be hashed")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("A source subclass must not be compared as a memo key")


def test_source_subclass_never_participates_in_a_warmed_key(module_reads: list[str]) -> None:
    analyzer, _ = _analyzed(FUNCTION)
    assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
    hostile = _HostileText(FUNCTION)
    assert not safety._module_may_replace_builtins(analyzer, hostile)
    assert not safety._module_may_replace_builtins(analyzer, hostile)
    assert len(module_reads) == 3


class _HostileAnalyzer(ScopeAnalyzer):
    def __hash__(self) -> int:
        raise AssertionError("An analyzer subclass must not be hashed")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("An analyzer subclass must not be compared as a memo key")


def test_analyzer_subclass_does_not_enter_the_owner_memo(module_reads: list[str]) -> None:
    analyzer = _HostileAnalyzer()
    analyzer.analyze(ast.parse(FUNCTION))
    assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
    assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
    assert len(module_reads) == 2


def test_each_owner_retains_only_its_most_recent_source(module_reads: list[str]) -> None:
    analyzer, _ = _analyzed(FUNCTION)
    second = "# another source\n" + FUNCTION
    for source in (FUNCTION, second, FUNCTION):
        assert not safety._module_may_replace_builtins(analyzer, source)
    assert len(module_reads) == 3


def test_a_cached_source_does_not_keep_its_analyzer_alive() -> None:
    analyzer, _ = _analyzed(FUNCTION)
    assert not safety._module_may_replace_builtins(analyzer, FUNCTION)
    assert analyzer in safety._BUILTIN_MAPPING_SOURCES
    owner = weakref.ref(analyzer)
    del analyzer
    gc.collect()
    assert owner() is None


@pytest.mark.parametrize("unicode_padding", ["", "😀" * 20], ids=["ascii", "unicode-reserve"])
def test_source_storage_admission_rejects_even_one_overweight_record(
    monkeypatch: pytest.MonkeyPatch, module_reads: list[str], unicode_padding: str
) -> None:
    source = "# " + unicode_padding + "\n" + FUNCTION
    analyzer, _ = _analyzed(source)
    storage = sys.getsizeof(source) + (0 if source.isascii() else 4 * len(source) + 1)
    monkeypatch.setattr(safety, "_BUILTIN_MAPPING_SOURCE_BYTES", storage - 1)
    assert not safety._module_may_replace_builtins(analyzer, source)
    assert not safety._module_may_replace_builtins(analyzer, source)
    assert len(module_reads) == 2
    monkeypatch.setattr(safety, "_BUILTIN_MAPPING_SOURCE_BYTES", storage)
    assert not safety._module_may_replace_builtins(analyzer, source)
    assert not safety._module_may_replace_builtins(analyzer, source)
    assert len(module_reads) == 3
