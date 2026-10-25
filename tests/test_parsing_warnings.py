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
from typing import Callable, List, Literal, Mapping, Optional, Tuple, Union
from unittest.mock import patch

import pytest

from towel import parsing_warnings
from towel.analysis_sources import (
    AnalysisSources,
    ParserConfiguration,
    memoized_source_analysis,
    parsing_is_pure,
)
from towel.unification.bounded_cache import BoundedCache, memoization_disabled
from towel.unification import module_bindings, namespace_writes, typing_forms

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
    r"value = rf'(?<!\w){value}(?!\w)'",
    r"value = FR'\400{value}\777'",
    r"value = rf'{{\w}}{value}{{\d}}'",
    r"value = rf'{value:{width}}\w'",
    r'''value = rf"\w{ {'key': 1} }\d"''',
    r'''value = rf"\w{ {1, 2} }\d"''',
    r'''value = rf"\w{'{'}\d"''',
    r"value = rf'\w{value=}\d'",
    r"value = rf'\w{value!r:>{width}}\d'",
    r"value = rf'\N{value}\w'",
    "literal_word = 'text'\n"
    r'''pattern = r"\{'c': 1\} not match for \{'b': 1\}"''' + "\n"
    "value = f'{literal_word}'\n",
    "# \\{ and \\} are only a comment\nvalue = f'{1}'\n",
    "pattern = r'''\\{\n\\}'''\nvalue = Fr'{1}'\n",
    r'''pattern = r"quote\"\{"''' + "\nvalue = RF'{1}'\n",
    "tag = f'{1}'\n# \\{ and \\} are only a comment\nvalue = 1\n",
    "tag = f'{1}'\npattern = r'''\\{\n\\}'''\n",
    "tag = f'{1}'\n" + r'''pattern = r"quote\"\{"''' + "\n",
    "tag = f'{1}'\n" + r'''pattern = br"\\\{[^{}]*\\\}"''' + "\n",
    'fields = {"t": "text"}\n' + r'''pattern = r"\{[^{}]*\}"''' + "\n",
    pytest.param(
        r'''value = f"{r'\{[^{}]*\}'}"''',
        marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 expression strings"),
    ),
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
    r"value = r'\{' + f'\{1}'",
    "value = r'\\{' + f'\\\r{1}'",
    "literal_word = 'text'\nvalue = '\\{'",
    "tag = f'{1}'\n" + r'''pattern = r"\{"; value = "\}"''',
    "tag = f'{1}'\n# \\{ is only a comment\n" + r'''value = f"\{1}"''',
    "tag = f'{1}'\n" + r'''pattern = r"quote\"\{"; value = f"\{1}"''',
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
            r"value = rf'\w{value:\q}\d'",
            r"value = rf'\w{value:{width}\q}\d'",
            r"""value = rf'\w{ {"key": 1}:\q}\d' """,
            r'''value = rf"\w{rf'{value:\q}'}\d"''',
        ]
    ],
]


class _CustomText(str):
    def __hash__(self) -> int:
        raise RuntimeError("Custom source hashing must not run")

    def __len__(self) -> int:
        raise RuntimeError("Custom source sizing must not run")

    def isascii(self) -> bool:
        raise RuntimeError("Custom source storage accounting must not run")

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        raise RuntimeError("Custom source encoding must not run")


class _CustomBytes(bytes):
    def __hash__(self) -> int:
        raise RuntimeError("Custom source hashing must not run")

    def __len__(self) -> int:
        raise RuntimeError("Custom source sizing must not run")

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        raise RuntimeError("Custom source decoding must not run")


@pytest.mark.parametrize("text", ["value = 1\n", "# 1name \\q\nvalue = 1\n"])
def test_lexical_subclasses_bypass_key_methods_without_touching_global_cache(text: str) -> None:
    source = _CustomText(text)
    before = tuple(parsing_warnings._CACHE.entries)
    for _ in range(2):
        assert parsing_warnings.parsing_cannot_warn(source)
        assert isinstance(ast.parse(source), ast.Module)
    assert tuple(parsing_warnings._CACHE.entries) == before


@pytest.mark.parametrize("kind", ["text", "bytes"])
@pytest.mark.parametrize("policy", ["always", "ignore", "error"])
@pytest.mark.parametrize(
    "text", ["value = 1\n", "# 1name \\q\nvalue = 1\n", "value = '\\q'\n", "value = @\n"]
)
def test_source_subclasses_preserve_native_trees_diagnostics_and_errors(
    kind: str, policy: Literal["always", "ignore", "error"], text: str
) -> None:
    native: Union[str, bytes] = text if kind == "text" else text.encode()
    source: Union[str, bytes] = _CustomText(text) if kind == "text" else _CustomBytes(text.encode())
    assert not parsing_is_pure(source)
    cache = AnalysisSources()

    def observe(
        operation: Callable[[], ast.Module],
    ) -> Tuple[Optional[str], Optional[Tuple[str, str]], List[Tuple[str, str, str, int]]]:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter(policy)
            result: Optional[str] = None
            failure: Optional[Tuple[str, str]] = None
            try:
                result = ast.dump(operation(), include_attributes=True)
            except (SyntaxError, ValueError, UnicodeError) as error:
                failure = (type(error).__name__, str(error))
        return (
            result,
            failure,
            [
                (item.category.__name__, str(item.message), item.filename, item.lineno)
                for item in emitted
            ],
        )

    for _ in range(2):
        expected = observe(lambda: ast.parse(native, filename="subclass.py"))
        actual = observe(lambda: cache.parse(source, "subclass.py"))
        assert actual == expected
    assert not cache._parsed


def test_memoized_analysis_recomputes_custom_text_without_hashing() -> None:
    calls = 0

    @memoized_source_analysis(4)
    def summarize(source: str) -> str:
        nonlocal calls
        calls += 1
        return ast.dump(ast.parse(source))

    source = _CustomText("value = 1\n")
    assert summarize(source) == summarize(source)
    assert calls == 2


def _source_analysis(name: str) -> Callable[[str], object]:
    return {
        "bindings": module_bindings.global_bindings,
        "facts": typing_forms._module_facts,
        "tree": namespace_writes._parsed,
    }[name]


def _analysis_value(value: object) -> object:
    return ast.dump(value, include_attributes=True) if isinstance(value, ast.Module) else value


@pytest.mark.parametrize("name", ["bindings", "facts", "tree"])
def test_manual_source_caches_do_not_hash_or_confuse_custom_text(name: str) -> None:
    class EqualText(str):
        def __hash__(self) -> int:
            return 1

        def __eq__(self, other: object) -> bool:
            return True

    analyze = _source_analysis(name)
    for source_class in (_CustomText, EqualText):
        for text in ("first = 1\n", "second = 2\n", "first = 1\n"):
            actual = analyze(source_class(text))
            with memoization_disabled():
                expected = analyze(text)
            assert _analysis_value(actual) == _analysis_value(expected)


@pytest.mark.parametrize("name", ["facts", "tree"])
@pytest.mark.parametrize("policy", ["always", "ignore", "error"])
def test_manual_source_cache_warning_hits_match_disabled_diagnostics(
    name: str, policy: Literal["always", "ignore", "error"]
) -> None:
    source = f"value_{name} = '\\q'\n"
    analyze = _source_analysis(name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert analyze(source) is not None
    for _ in range(2):
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter(policy)
            actual = analyze(source)
        with warnings.catch_warnings(record=True) as expected_emitted:
            warnings.simplefilter(policy)
            with memoization_disabled():
                expected = analyze(source)
        assert _analysis_value(actual) == _analysis_value(expected)
        assert [
            (item.category.__name__, str(item.message), item.filename, item.lineno)
            for item in emitted
        ] == [
            (item.category.__name__, str(item.message), item.filename, item.lineno)
            for item in expected_emitted
        ]


@pytest.mark.parametrize("name", ["facts", "tree"])
def test_manual_source_cache_tracks_integer_parser_limits(name: str) -> None:
    source = f"value_{name} = " + "7" * 700 + "\n"
    analyze = _source_analysis(name)
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(0)
        assert analyze(source) is not None
        sys.set_int_max_str_digits(640)
        assert analyze(source) is None
        sys.set_int_max_str_digits(0)
        assert analyze(source) is not None
    finally:
        sys.set_int_max_str_digits(previous)


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


def _parse_observation(
    source: str,
    policy: Literal["always", "error", "ignore"],
    cache: AnalysisSources | None,
) -> tuple[object, tuple[object, ...]]:
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


@pytest.mark.parametrize("source", WARNING_SOURCES)
@pytest.mark.parametrize("policy", ["always", "error", "ignore"])
def test_native_warning_sources_are_never_shared(
    source: str, policy: Literal["always", "error", "ignore"]
) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert not parsing_is_pure(source)
    assert not emitted

    expected = _parse_observation(source, policy, None)
    cache = AnalysisSources()
    assert _parse_observation(source, policy, cache) == expected
    assert _parse_observation(source, policy, cache) == expected


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


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 expression strings")
@pytest.mark.parametrize(
    "source",
    [
        r'''value = rf"\w{rf'\d{value}'}\q"''',
        r'''value = f"{rf'\w{value}'}"''',
        r'''value = rf"\w{ {'key': rf'\d{value}'} }\q"''',
        'value = rf"\\w{value # comment }\n}\\d"',
    ],
)
def test_nested_raw_literal_frames_preserve_native_parses(source: str) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        expected = ast.parse(source, filename="nested.py")
        assert parsing_is_pure(source)
        cache = AnalysisSources()
        first = cache.parse(source, "nested.py")
        assert cache.parse(source, "nested.py") is first
        assert ast.dump(first, include_attributes=True) == ast.dump(
            expected, include_attributes=True
        )
    assert not emitted


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 expression strings")
@pytest.mark.parametrize(
    "source",
    [
        r'''value = rf"\w{f'{value:\q}'}\d"''',
        r'''value = rf"\w{'\q'=}\d"''',
        r'''value = rf"\w{ {'key': '\q'} }\d"''',
    ],
)
def test_raw_literal_frames_do_not_hide_nested_native_warnings(source: str) -> None:
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


@pytest.mark.parametrize("prefix", ["f", "fr", "rf", "F", "Fr", "fR", "FR", "Rf", "rF", "RF"])
@pytest.mark.parametrize("separator", ["", "\r"])
def test_early_raw_brace_does_not_hide_a_later_formatted_escape(
    prefix: str, separator: str
) -> None:
    source = "literal_word = 'text'\nvalue = r'\\{' + " + prefix + "'\\" + separator + "{1}'"
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert parsing_is_pure(source) == (
            "r" in prefix.lower() and not separator and sys.version_info >= (3, 12)
        )
    assert not emitted


@pytest.mark.skipif(sys.version_info < (3, 14), reason="template strings")
@pytest.mark.parametrize("prefix", ["t", "tr", "rt", "T", "Tr", "tR", "TR", "Rt", "rT", "RT"])
def test_early_raw_brace_does_not_hide_a_later_template_escape(prefix: str) -> None:
    source = "literal_word = 'text'\nvalue = r'\\{' + " + prefix + "'\\{1}'"
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert parsing_is_pure(source) == ("r" in prefix.lower())
    assert not emitted


FORMATTED_PREFIXES = [
    "f",
    "fr",
    "rf",
    "F",
    "Fr",
    "fR",
    "FR",
    "Rf",
    "rF",
    "RF",
    *[
        pytest.param(
            prefix,
            marks=pytest.mark.skipif(sys.version_info < (3, 14), reason="template strings"),
        )
        for prefix in ("t", "tr", "rt", "T", "Tr", "tR", "TR", "Rt", "rT", "RT")
    ],
]


@pytest.mark.parametrize("prefix", FORMATTED_PREFIXES)
@pytest.mark.parametrize("backslashes", [1, 2, 3, 4])
@pytest.mark.parametrize("separator", ["", "\r"])
@pytest.mark.parametrize("closing", [False, True])
def test_complete_backslash_runs_preserve_native_formatted_diagnostics(
    prefix: str, backslashes: int, separator: str, closing: bool
) -> None:
    run = "\\" * backslashes + separator
    body = "{1}" + run + "}}" if closing else run + "{1}"
    # An innocuous numeric-looking comment exercises the lexical proof even
    # when a carriage return would otherwise take the no-warning fast path.
    source = "# 1name is only a comment\nvalue = " + prefix + '"""' + body + '"""'
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        for _ in range(2):
            assert parsing_is_pure(source) == (
                backslashes % 2 == 0
                or ("r" in prefix.lower() and not separator and sys.version_info >= (3, 12))
            )
    assert not emitted
    for policy in ("always", "ignore", "error"):
        expected = _parse_observation(source, policy, None)
        cache = AnalysisSources()
        for _ in range(2):
            assert _parse_observation(source, policy, cache) == expected
        if backslashes % 2 == 0:
            assert isinstance(expected[0], str) and not expected[1]


@pytest.mark.parametrize("prefix", FORMATTED_PREFIXES)
@pytest.mark.parametrize("separator", ["", "\r"])
def test_even_run_does_not_hide_a_later_odd_formatted_escape(prefix: str, separator: str) -> None:
    source = "value = " + prefix + '"""\\\\{1}""" + ' + prefix + '"""\\' + separator + '{2}"""'
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert parsing_is_pure(source) == (
            "r" in prefix.lower() and not separator and sys.version_info >= (3, 12)
        )
    assert not emitted
    for policy in ("always", "ignore", "error"):
        expected = _parse_observation(source, policy, None)
        cache = AnalysisSources()
        assert _parse_observation(source, policy, cache) == expected
        assert _parse_observation(source, policy, cache) == expected


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 expression strings")
@pytest.mark.parametrize(
    "source, pure",
    [
        (r'''value = f"\\{f'\\{2}'}"''', True),
        (r'''value = f"\\{f'\{2}'}"''', False),
        (r'''value = f"\\{'\q'}"''', False),
        (r'''value = rf"\{f'\{2}'}"''', False),
        (r'''value = rf"\{'\{'}"''', False),
        (r'''value = rf"\{'\q'}"''', False),
        (r'''value = rf"\{1if True else 0}"''', False),
        (r'''value = rf"\{1}" + f"\{2}"''', False),
        (r"""value = rf"\{1}" + '\q' """, False),
        (r'''value = f"{rf'\{2}'}"''', True),
        (r'''value = rf"\{ {'key': rf'\{2}'} }"''', True),
        (r'''value = rf"\{1:\q}"''', sys.version_info[:2] != (3, 12)),
        (r'''value = rf"\{1:\400}"''', sys.version_info[:2] != (3, 12)),
        (r'''value = rf"{1:\{2}}"''', False),
    ],
)
def test_shadow_ownership_preserves_nested_native_effects(source: str, pure: bool) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert parsing_is_pure(source) is pure
    assert not emitted
    for policy in ("always", "ignore", "error"):
        expected = _parse_observation(source, policy, None)
        cache = AnalysisSources()
        assert _parse_observation(source, policy, cache) == expected
        assert _parse_observation(source, policy, cache) == expected


@pytest.mark.skipif(sys.version_info < (3, 12), reason="formatted string token ownership")
@pytest.mark.parametrize(
    "source",
    [
        r"value = rf'\{x}'",
        r"value = rf'{x}\}}'",
        r"value = rf'\{{{x}}}'",
        r'''value = f"{rf'\{x}'}"''',
    ],
)
def test_raw_formatted_braces_reuse_original_ast(source: str) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        expected = ast.parse(source, filename="raw.py")
        assert parsing_is_pure(source)
        cache = AnalysisSources()
        first = cache.parse(source, "raw.py")
        assert cache.parse(source, "raw.py") is first
        assert ast.dump(first, include_attributes=True) == ast.dump(
            expected, include_attributes=True
        )
        changed = source + "\nadded = 1\n"
        assert ast.dump(cache.parse(changed, "raw.py"), include_attributes=True) == ast.dump(
            ast.parse(changed, filename="raw.py"), include_attributes=True
        )
    assert not emitted


@pytest.mark.parametrize("source", [r"value = rf'\{x", r"value = rf'\{x]}'", r"value = rf'\}'"])
def test_raw_formatted_shadow_rejects_malformed_frames(source: str) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert not parsing_is_pure(source)
    assert not emitted


def test_later_ordinary_raw_braces_reuse_parses_without_changing_diagnostics() -> None:
    source = "tag = f'{1}'\n" + r'''pattern = r"\{[^{}]*\}"''' + "\n"
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert parsing_is_pure(source)
    assert not emitted
    for policy in ("always", "ignore", "error"):
        expected = _parse_observation(source, policy, None)
        assert isinstance(expected[0], str) and not expected[1]
        cache = AnalysisSources()
        assert _parse_observation(source, policy, cache) == expected
        assert _parse_observation(source, policy, cache) == expected


def test_raw_brace_fallback_keeps_carriage_returns_conservative() -> None:
    source = "tag = f'{1}'\r\n" + r'''pattern = r"\{[^{}]*\}"''' + "\n"
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert not parsing_is_pure(source)
    assert not emitted
    for policy in ("always", "ignore", "error"):
        expected = _parse_observation(source, policy, None)
        assert isinstance(expected[0], str) and not expected[1]
        cache = AnalysisSources()
        assert _parse_observation(source, policy, cache) == expected
        assert _parse_observation(source, policy, cache) == expected


@pytest.mark.parametrize(
    "literal_source",
    [
        "literal_word = 'text'\n"
        r'''pattern = r"\{'c': 1\} not match for \{'b': 1\}"''' + "\n"
        "value = f'{literal_word}'\n",
        r'''value = f"{value}\\{text}"''' + "\n",
        'fields = {"t": "text"}\n' + r'''pattern = r"\{[^{}]*\}"''' + "\n",
        "tag = f'{1}'\n" + r'''pattern = r"\{[^{}]*\}"''' + "\n",
        r'''value = f"\x1b]8;id={value};{text}\x1b\\{text}\x1b]8;;\x1b\\"''' + "\n",
        *[
            pytest.param(
                "class Translator:\n    def visit(self, short, tag):\n"
                "        self.body.append(" + literal + ")\n",
                marks=pytest.mark.skipif(
                    sys.version_info < (3, 12), reason="formatted string token ownership"
                ),
                id=name,
            )
            for name, literal in [
                ("sphinx-section", r"rf'\{self.sectionnames[self.sectionlevel]}{short}{{'"),
                ("sphinx-last-section", r"rf'\{self.sectionnames[-1]}{short}{{'"),
                ("sphinx-tag", r"rf'\{tag}*{{'"),
            ]
        ],
    ],
    ids=[
        "raw-regex",
        "markup",
        "prettytable-prefix",
        "later-raw-regex",
        "ansi-link",
        "sphinx-section",
        "sphinx-last-section",
        "sphinx-tag",
    ],
)
def test_warning_free_module_reuses_facts_and_recomputes_changed_or_uncached_queries(
    literal_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "from typing import Optional\n" + literal_source
    facts: BoundedCache[Tuple[str, ParserConfiguration], Optional[typing_forms._ModuleFacts]] = (
        BoundedCache(256)
    )
    monkeypatch.setattr(typing_forms, "_FACTS", facts)
    original = typing_forms._scoped_bindings
    constructions = 0

    def count_constructions(
        tree: ast.Module,
    ) -> Tuple[Mapping[str, Tuple[typing_forms._ScopedBinding, ...]], Tuple[Optional[str], ...]]:
        nonlocal constructions
        constructions += 1
        return original(tree)

    monkeypatch.setattr(typing_forms, "_scoped_bindings", count_constructions)
    spellings = ("Optional", "unrelated")
    with memoization_disabled():
        expected = tuple(typing_forms._local_forms(source, spelling) for spelling in spellings)
    assert constructions == len(spellings)

    constructions = 0
    for _ in range(4):
        assert (
            tuple(typing_forms._local_forms(source, spelling) for spelling in spellings) == expected
        )
    assert constructions == 1

    changed = source + "from typing import Optional as renamed_optional\n"
    assert typing_forms._local_forms(changed, "renamed_optional") == expected[0]
    assert constructions == 2

    with memoization_disabled():
        assert (
            tuple(typing_forms._local_forms(source, spelling) for spelling in spellings) == expected
        )
    assert constructions == 2 + len(spellings)


def test_prettytable_raw_pattern_reuses_global_bindings_without_reparsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = 'fields = {"t": "text"}\n' + r'''pattern = r"\{[^{}]*\}"''' + "\n"
    cache: BoundedCache[
        Tuple[str, ParserConfiguration], Optional[module_bindings.ModuleBindings]
    ] = BoundedCache(128)
    monkeypatch.setattr(module_bindings, "_MODULE_BINDINGS", cache)
    with memoization_disabled():
        expected = module_bindings.global_bindings(source)
    assert expected is not None
    with patch.object(ast, "parse", wraps=ast.parse) as parse:
        for _ in range(4):
            assert module_bindings.global_bindings(source) == expected
        assert parse.call_count == 1
        changed = module_bindings.global_bindings(source + "added = 1\n")
        assert changed is not None and changed.may_bind("added")
        assert not expected.may_bind("added")
        assert parse.call_count == 2
        with memoization_disabled():
            assert module_bindings.global_bindings(source) == expected
        assert parse.call_count == 3
