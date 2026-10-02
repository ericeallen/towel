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

"""Warning eligibility is pure, bounded, and preserves actual native diagnostics."""

import ast
import sys
import warnings
from typing import Literal

import pytest

from towel import parsing_warnings
from towel.analysis_sources import AnalysisSources, parsing_is_pure
from towel.unification.bounded_cache import memoization_disabled

SAFE_SOURCES = [
    "# 3.x and \\d are only a comment\nvalue = 1\n",
    "python3args = 1\nfloat32_value = python3args\n",
    "value = 1e3 + 0xff + 1j\n",
    r"value = r'\d\400'",
    r"value = br'\d\u1234'",
    r"value = '\\d\\400'",
    r"value = '\378\0009'",
    r"value = '\u1234\U00001234\N{RIGHT CURLY BRACKET}'",
    "value = '<object at 0x{:x}>'\n",
    "value = f'object at 0x{id(object):x}'\n",
    "value = f'{1e3} {0xff} {1j}'\n",
    "pattern = r'\\(([^()]*)\\)'\nvalue = f'object at 0x{id(object):x}'\n",
]

WARNING_SOURCES = [
    r"value = '\d'",
    r"value = '\400'",
    r"value = '\777'",
    r"value = b'\u1234'",
    r"value = b'\U00001234'",
    r"value = b'\N{SPACE}'",
    r"value = f'\{1}'",
    r"value = f'\d{1}'",
    r"value = f'{1:\d}'",
    "value = 1if True else 0",
    "value = 1.and 2",
    "value = 1not in ()",
    "value = [1for item in ()]",
    "value = 1e1if True else 0",
    "value = 1jif True else 0",
    "value = 0xffif True else 0",
    "value = f'{0xffif True else 0}'",
    "value = f'{1jif True else 0}'",
    "value = f'{1not in ()}'",
    "value = f'{1if True else 0}'",
    "value = '\\d'\nbroken =",
    *[
        pytest.param(
            source,
            marks=pytest.mark.skipif(
                sys.version_info[:2] != (3, 12),
                reason="CPython 3.12 decodes raw f-string format specifications",
            ),
        )
        for source in [
            r"value = fr'{value:\q}'",
            r"value = rf'{value:\400}'",
            r"value = FR'{value:{width}\q}'",
            r"value = Rf'{value:{width}\777}'",
            "value = fr'{value:\\q}'\nbroken =",
            "value = rf'{value:\\400}'\nbroken =",
        ]
    ],
]


@pytest.mark.parametrize("source", SAFE_SOURCES)
def test_safe_lexical_contexts_reuse_the_native_tree(source: str) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert parsing_is_pure(source)
        expected = ast.parse(source)
        cache = AnalysisSources()
        first = cache.parse(source, "safe.py")
        assert cache.parse(source, "safe.py") is first
        assert ast.dump(first) == ast.dump(expected)
    assert not emitted


@pytest.mark.parametrize("source", WARNING_SOURCES)
@pytest.mark.parametrize("policy", ["always", "error", "ignore"])
def test_native_warning_sources_are_never_shared(
    source: str, policy: Literal["always", "error", "ignore"]
) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert not parsing_is_pure(source)
    assert not emitted

    def observe(cache: AnalysisSources | None) -> tuple[object, tuple[object, ...]]:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter(policy)
            try:
                tree = (
                    ast.parse(source, filename="subject.py")
                    if cache is None
                    else cache.parse(source, "subject.py")
                )
                result: object = ast.dump(tree, include_attributes=True)
            except (SyntaxError, Warning) as error:
                result = (type(error), error.args)
        return result, tuple((w.category, str(w.message), w.filename, w.lineno) for w in emitted)

    expected = observe(None)
    cache = AnalysisSources()
    assert observe(cache) == expected
    assert observe(cache) == expected


@pytest.mark.parametrize("separator", ["", "\r"])
@pytest.mark.parametrize("brace", ["{", "}"])
def test_tokenizer_warning_guard_has_no_diagnostics(separator: str, brace: str) -> None:
    source = "# 1if\nvalue = f'\\" + separator + brace + "1}'"
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert not parsing_is_pure(source)
    assert not emitted


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 expression strings")
def test_raw_formatted_literal_does_not_hide_nonraw_nested_strings() -> None:
    source = "value = rf\"{'\\d'}\""
    assert not parsing_is_pure(source)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        ast.parse(source)
    assert len(emitted) == 1


@pytest.mark.skipif(sys.version_info < (3, 14), reason="template strings")
def test_template_literals_and_expressions_keep_separate_escape_rules() -> None:
    assert parsing_is_pure("value = t'object at 0x{42:x}'")
    assert parsing_is_pure(r"value = rt'\d{42}'")
    assert not parsing_is_pure(r"value = t'\d{42}'")
    assert not parsing_is_pure("value = rt\"{'\\d'}\"")


def test_boolean_cache_reuses_exact_text_and_honors_no_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsing_warnings, "_CACHE", parsing_warnings._LexicalCache())
    original = parsing_warnings._tokens_cannot_warn
    seen: list[str] = []

    def observe(source: str) -> bool:
        seen.append(source)
        return original(source)

    monkeypatch.setattr(parsing_warnings, "_tokens_cannot_warn", observe)
    source = "value = r'\\d'"
    assert parsing_is_pure(source)
    assert parsing_is_pure(source)
    assert seen == [source]
    with memoization_disabled():
        assert parsing_is_pure(source)
        assert parsing_is_pure(source)
    assert seen == [source] * 3
    assert not parsing_is_pure("value = '\\d'")
    assert seen[-1] == "value = '\\d'"


def test_retained_source_has_both_entry_and_size_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = parsing_warnings._LexicalCache(max_entries=2)
    monkeypatch.setattr(parsing_warnings, "_CACHE", cache)
    for index in range(3):
        assert parsing_is_pure(f"value{index} = r'\\d'")
    assert len(cache.entries) == 2
    before = tuple(cache.entries)
    source = "# " + "x" * parsing_warnings._MAX_SOURCE_CHARACTERS + "\nvalue = r'\\d'"
    assert parsing_is_pure(source)
    assert tuple(cache.entries) == before
