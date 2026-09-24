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

"""The comments of moved code go with it into the helper.

A helper is rendered from its syntax tree, which holds no comments, so a
``# type: ignore`` inside a duplicate block used to vanish with the block
(asyncstdlib's heapq, mashumaro's type_name, packaging's ``_from_dict``), as
did ``# pragma: no cover``, ``# pyright: ignore``, ``# noqa`` and every
explanatory comment. Each is now written into the helper beside the code it
was written for, and the formatter must leave a directive there. A
directive every site carries moves; one the sites disagree about declines
the pair, since the helper has one line where the sites had several.
"""

from __future__ import annotations

import ast
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import textwrap
import tokenize
from typing import Dict, Mapping, Sequence

import pytest

from towel.diagnostics import Settings
from towel.formatting import BlackSettings, SnippetFormatter, black_formatter, checked
from towel.unification.refactor_engine import UnificationRefactorEngine

SERIAL = Settings.from_environ({"TOWEL_WORKERS": "1"})

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_ruff = pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="ruff absent")


def _without_comments(source: str) -> str:
    """``source`` with every comment removed and nothing else changed."""
    lines = source.split("\n")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            line, column = token.start
            lines[line - 1] = lines[line - 1][:column].rstrip()
    return "\n".join(lines)


def _engine(**options: object) -> UnificationRefactorEngine:
    return UnificationRefactorEngine(min_lines=3, settings=SERIAL, **options)  # type: ignore[arg-type]


def _refactor(path: Path, **options: object) -> str:
    engine = _engine(**options)
    proposals = engine.analyze_file(str(path))
    assert proposals, f"no proposal; declined: {engine.declined_pairs}"
    return engine.apply_refactoring(str(path), proposals[0])


def _helper_source(result: str) -> str:
    """The text of the helper the refactoring inserted into ``result``."""
    lines = result.split("\n")
    (helper,) = [
        node
        for node in ast.walk(ast.parse(result))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    return "\n".join(lines[helper.lineno - 1 : helper.end_lineno])


def _line_holding(text: str, fragment: str) -> str:
    (line,) = [line for line in text.split("\n") if fragment in line]
    return line


def _write(tmp_path: Path, source: str, name: str = "m.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _assert_same_refactoring_without_comments(path: Path, result: str, **options: object) -> None:
    """The comments change only what is written: the tree is the comment-free input's."""
    bare = path.with_name("bare_" + path.name)
    bare.write_text(_without_comments(path.read_text(encoding="utf-8")), encoding="utf-8")
    assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(_refactor(bare, **options)))


_LOOP = """
    def first(values):
        total = 0
        for value in values:
            total += value * 2{comment}
        return total + 1


    def second(values):
        total = 0
        for value in values:
            total += value * 2{comment}
        return total + 1
    """

_DIRECTIVES = [
    "# type: ignore[operator]",
    "# pyright: ignore[reportOperatorIssue]",
    "# noqa: E501",
    "# pragma: no cover",
    "# nosec",
    "# pylint: disable=invalid-name",
    "# isort: skip",
    "# fmt: skip",
    "# type: ignore[operator]  # noqa: E501",
]


@pytest.mark.parametrize("directive", _DIRECTIVES)
def test_a_directive_every_site_carries_stays_on_its_line_in_the_helper(
    tmp_path: Path, directive: str
) -> None:
    path = _write(tmp_path, _LOOP.format(comment="  " + directive))
    result = _refactor(path)
    helper = _helper_source(result)
    assert _line_holding(helper, "total += value * 2").endswith("  " + directive), helper
    # Moved, once: neither call site keeps a copy of the block's comment.
    assert result.count(directive) == 1, result
    _assert_same_refactoring_without_comments(path, result)


@pytest.mark.parametrize("formatter", ["black", "ruff"])
@pytest.mark.parametrize("directive", _DIRECTIVES)
def test_the_formatter_leaves_each_directive_on_its_line(
    tmp_path: Path, directive: str, formatter: str
) -> None:
    if importlib.util.find_spec(formatter) is None:
        pytest.skip(f"{formatter} absent")
    path = _write(tmp_path, _LOOP.format(comment="  " + directive))
    result = _refactor(path, snippet_formatter=_formatter(formatter, path))
    helper = _helper_source(result)
    assert _line_holding(helper, "total += value * 2").endswith("  " + directive), helper
    _assert_same_refactoring_without_comments(
        path, result, snippet_formatter=_formatter(formatter, path)
    )


def _formatter(name: str, path: Path) -> SnippetFormatter:
    if name == "black":
        return black_formatter(BlackSettings())
    from towel.formatting import ruff_formatter

    return ruff_formatter(path)


_EXPLAINED = """
    def first(values, limit):
        # Start from nothing.
        total = 0
        for value in values:
            if value > limit:  # large values count double
                total += value * 2
                # the bonus for a large value
            else:
                total += value
        return total + 1


    def second(values, limit):
        # Start from nothing.
        total = 0
        for value in values:
            if value > limit:  # large values count double
                total += value * 2
                # the bonus for a large value
            else:
                total += value
        return total + 1
    """


def test_explanatory_comments_keep_their_statements_and_order(tmp_path: Path) -> None:
    path = _write(tmp_path, _EXPLAINED)
    result = _refactor(path)
    helper = _helper_source(result)
    assert helper.split("\n")[1:3] == ["    total = 0", "    for value in values:"], helper
    assert _line_holding(helper, "if value > limit:").endswith("  # large values count double")
    lines = helper.split("\n")
    bonus = lines.index("            # the bonus for a large value")
    assert lines[bonus - 1].strip() == "total += value * 2"
    assert lines[bonus + 1].strip() == "else:"
    assert result.count("# the bonus for a large value") == 1
    assert result.count("# large values count double") == 1
    # A comment above the block is the call site's own, and stays with each call.
    assert result.count("    # Start from nothing.\n    return __extracted_func_0(") == 2, result
    _assert_same_refactoring_without_comments(path, result)


_CLAUSES = """
    def first(values):
        found = []
        for value in values:
            try:  # the parse may fail
                found.append(int(value))
            # only malformed numbers are skipped
            except ValueError:  # pragma: no cover
                found.append(0)
            else:  # pragma: no cover
                found.append(1)
            finally:  # always counted
                found.append(-1)
        return found


    def second(values):
        found = []
        for value in values:
            try:  # the parse may fail
                found.append(int(value))
            # only malformed numbers are skipped
            except ValueError:  # pragma: no cover
                found.append(0)
            else:  # pragma: no cover
                found.append(1)
            finally:  # always counted
                found.append(-1)
        return found
    """


@pytest.mark.parametrize("formatted", [False, True])
def test_comments_on_clause_headers_stay_on_their_headers(tmp_path: Path, formatted: bool) -> None:
    path = _write(tmp_path, _CLAUSES)
    options: Dict[str, object] = (
        {"snippet_formatter": black_formatter(BlackSettings())} if formatted else {}
    )
    result = _refactor(path, **options)
    helper = _helper_source(result)
    lines = [line.strip() for line in helper.split("\n")]
    assert "try:  # the parse may fail" in lines, helper
    assert "except ValueError:  # pragma: no cover" in lines
    assert "else:  # pragma: no cover" in lines
    assert "finally:  # always counted" in lines
    assert lines.index("# only malformed numbers are skipped") + 1 == lines.index(
        "except ValueError:  # pragma: no cover"
    )
    _assert_same_refactoring_without_comments(path, result, **options)


_CALLS = """
    def first(numerator, denominator):
        ratio = numerator / denominator
        return format_ratio(
            numerator,
            denominator,
            product_fmt="*",  # TODO: should this be ''?
            power_fmt="{{}}**{{}}",  # the power, with no spaces
            parentheses_fmt=r"({{}})",
        )


    def second(numerator, denominator):
        ratio = numerator / denominator
        return format_ratio(
            numerator,
            denominator,
            product_fmt="{{}} * {{}}",
            power_fmt="{{}} ** {{}}",  # the power, with no spaces
            parentheses_fmt=r"({{}})",
        )
    """


@pytest.mark.parametrize("formatter", [None, "black", "ruff"])
def test_a_comment_inside_brackets_stays_beside_its_argument(
    tmp_path: Path, formatter: str | None
) -> None:
    if formatter is not None and importlib.util.find_spec(formatter) is None:
        pytest.skip(f"{formatter} absent")
    path = _write(tmp_path, _CALLS.format())
    options: Dict[str, object] = (
        {} if formatter is None else {"snippet_formatter": _formatter(formatter, path)}
    )
    result = _refactor(path, **options)
    helper = _helper_source(result)
    # One site's note on the argument that now differs is kept, beside the
    # parameter that took the argument's place; both sites' shared note once.
    assert "product_fmt=" in _line_holding(helper, "# TODO: should this be ''?")
    assert "power_fmt=" in _line_holding(helper, "# the power, with no spaces")
    assert result.count("# the power, with no spaces") == 1
    _assert_same_refactoring_without_comments(path, result, **options)


_GROUPED = """
    from typing import Any, Awaitable, Callable, Optional


    def first(key: Optional[Callable[[Any], Awaitable[Any]]]) -> str:
        a_key: Callable[[Any], Awaitable[Any]] = (
            awaitify(key) if key is not None else _identity  # type: ignore
        )
        name = repr(a_key)
        return name.upper()


    def second(key: Optional[Callable[[Any], Awaitable[Any]]]) -> str:
        a_key: Callable[[Any], Awaitable[Any]] = (
            awaitify(key) if key is not None else _identity  # type: ignore
        )
        name = repr(a_key)
        return name.upper()
    """


@pytest.mark.parametrize("formatter", ["black", "ruff"])
def test_an_ignore_inside_grouping_parentheses_keeps_them_under_either_formatter(
    tmp_path: Path, formatter: str
) -> None:
    """asyncstdlib's heapq: ruff splits the line and would move the ignore past the parenthesis."""
    if importlib.util.find_spec(formatter) is None:
        pytest.skip(f"{formatter} absent")
    path = _write(tmp_path, _GROUPED)
    result = _refactor(path, snippet_formatter=_formatter(formatter, path))
    helper = _helper_source(result)
    assert (
        "        awaitify(key) if key is not None else _identity  # type: ignore" in helper
    ), helper
    _assert_same_refactoring_without_comments(
        path, result, snippet_formatter=_formatter(formatter, path)
    )


def test_a_formatter_that_moves_a_directive_is_not_used_for_that_helper(tmp_path: Path) -> None:
    """The helper is inserted as rendered when formatting would split a directive's line."""
    source = textwrap.dedent("""
        def first(values):
            total = 0
            total += sum(values, 0) + len(values) + max(values) + min(values)  # pyright: ignore
            return total + 1


        def second(values):
            total = 0
            total += sum(values, 0) + len(values) + max(values) + min(values)  # pyright: ignore
            return total + 1
        """).lstrip()

    def splitting(text: str) -> str:
        # A formatter that splits every long line and leaves its comment at the end.
        out = []
        for line in text.split("\n"):
            code, hash_, comment = line.partition("  #")
            if hash_ and "sum(" in code:
                head, _, tail = code.partition("sum(")
                out.append(head + "sum(")
                out.append("    " + tail + "  #" + comment)
            else:
                out.append(line)
        return "\n".join(out)

    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    result = _refactor(path, snippet_formatter=checked(splitting))
    helper = _helper_source(result)
    assert "total += sum(values, 0)" in _line_holding(helper, "# pyright: ignore"), helper


def test_comments_that_differ_between_sites_are_all_kept_in_site_order(tmp_path: Path) -> None:
    """tinydb's query path: each site's own note on the same statement stays."""
    path = _write(
        tmp_path,
        """
        class Query:
            def __getattr__(self, item):
                query = type(self)()

                # Now we add the accessed item to the query path ...
                query._path = self._path + (item,)
                query._hash = None
                return query

            def map(self, fn):
                query = type(self)()

                # Now we add the callable to the query path ...
                query._path = self._path + (fn,)
                query._hash = None
                return query
        """,
    )
    result = _refactor(path)
    helper = _helper_source(result).split("\n")
    first = helper.index("        # Now we add the accessed item to the query path ...")
    assert helper[first + 1] == "        # Now we add the callable to the query path ..."
    assert helper[first + 2].strip().startswith("query._path = self._path + (")
    _assert_same_refactoring_without_comments(path, result)


def _declined(path: Path, source: str = "", **options: object) -> Mapping[str, int]:
    if source:
        path = _write(path.parent, source, path.name)
    engine = _engine(**options)
    proposals = engine.analyze_file(str(path))
    assert not proposals, [ast.unparse(proposal.extracted_function) for proposal in proposals]
    return engine.declined_pairs


def test_a_directive_only_one_site_carries_declines_the_pair(tmp_path: Path) -> None:
    """mashumaro's type_name: the ignore one copy needs would silence the other's line too."""
    path = _write(tmp_path, _LOOP.format(comment="{comment}").format(comment=""))
    text = path.read_text(encoding="utf-8").replace(
        "total += value * 2\n", "total += value * 2  # type: ignore[operator]\n", 1
    )
    path.write_text(text, encoding="utf-8")
    assert "directives_differ" in _declined(path)


def test_directives_written_differently_decline_the_pair(tmp_path: Path) -> None:
    path = _write(tmp_path, _LOOP.format(comment="  # noqa: E501"))
    text = path.read_text(encoding="utf-8")
    at = text.rindex("# noqa: E501")
    path.write_text(text[:at] + "# noqa: E741" + text[at + len("# noqa: E501") :])
    assert "directives_differ" in _declined(path)


def test_a_directive_spaced_differently_is_the_same_directive(tmp_path: Path) -> None:
    path = _write(tmp_path, _LOOP.format(comment="  # type: ignore[operator]"))
    text = path.read_text(encoding="utf-8")
    at = text.rindex("# type: ignore[operator]")
    path.write_text(text[:at] + "#type:ignore[operator]" + text[at + 24 :])
    result = _refactor(path)
    assert _line_holding(_helper_source(result), "total += value * 2").endswith(
        "  # type: ignore[operator]"
    )


def test_an_ignore_over_code_that_becomes_an_argument_declines_the_pair(tmp_path: Path) -> None:
    """The differing call would be written at the call site, where the ignore does not reach."""
    path = _write(
        tmp_path,
        """
        def first(record):
            total = 0
            total += record.alpha.count()  # type: ignore[attr-defined]
            total = total * 2
            return total


        def second(record):
            total = 0
            total += record.beta.count()  # type: ignore[attr-defined]
            total = total * 2
            return total
        """,
    )
    assert "directive_on_argument" in _declined(path)


def test_an_ignore_over_a_name_that_becomes_an_argument_moves(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        def first(record, alpha):
            total = 0
            total += count(alpha)  # type: ignore[arg-type]
            total = total * 2
            return total


        def second(record, beta):
            total = 0
            total += count(beta)  # type: ignore[arg-type]
            total = total * 2
            return total
        """,
    )
    result = _refactor(path)
    assert _line_holding(_helper_source(result), "total += count(").endswith(
        "  # type: ignore[arg-type]"
    )


_REGION = """
    def first(values):
        total = 0
        # fmt: off
        total += sum( values )
        # fmt: on
        return total + 1


    def second(values):
        total = 0
        # fmt: off
        total += sum( values )
        # fmt: on
        return total + 1
    """


def test_a_region_directive_closed_inside_the_block_moves_whole(tmp_path: Path) -> None:
    path = _write(tmp_path, _REGION)
    result = _refactor(path, snippet_formatter=black_formatter(BlackSettings()))
    helper = [line.strip() for line in _helper_source(result).split("\n")]
    off = helper.index("# fmt: off")
    assert helper[off + 1].startswith("total += sum(") and helper[off + 2] == "# fmt: on"


def test_a_region_directive_reaching_past_the_block_declines_the_pair(tmp_path: Path) -> None:
    """``# fmt: off`` opened in the block would stop covering the code after it."""
    path = _write(
        tmp_path,
        """
        def first(values):
            total = 0
            # fmt: off
            total += sum( values )
            total = total * 2
            print( total )
            # fmt: on
            return total


        def second(values):
            total = 0
            # fmt: off
            total += sum( values )
            total = total * 2
            print( total )
            # fmt: on
            return total
        """,
    )
    engine = _engine()
    for proposal in engine.analyze_file(str(path)):
        spans = {replacement.line_range for replacement in proposal.replacements}
        # No block that holds one of the region's ends without the other moves.
        for start, end in spans:
            lines = path.read_text().split("\n")[start - 1 : end]
            assert any("fmt: off" in line for line in lines) == any(
                "fmt: on" in line for line in lines
            )
    assert "directive_outlives_block" in engine.declined_pairs


def _package(root: Path, comment: str) -> Sequence[Path]:
    block = """
        def {name}(values):
            print({tag!r})
            total = 0
            for value in values:
                if value > 1:
                    total += value * 2{comment}
                else:
                    total -= value
            return total
        """
    (root / "zzshared").mkdir(parents=True)
    (root / "zzshared" / "__init__.py").write_text("")
    paths = []
    for name in ("first", "second"):
        path = root / "zzshared" / f"{name}.py"
        path.write_text(
            textwrap.dedent(block.format(name=f"total_{name}", tag=name, comment=comment)).lstrip()
        )
        paths.append(path)
    return paths


def test_a_helper_shared_across_modules_carries_the_comments(tmp_path: Path) -> None:
    paths = _package(tmp_path / "project", "  # noqa: E501")
    engine = _engine(cross_module_helpers=True)
    (proposal,) = engine.analyze_files([str(path) for path in paths], progress="none")
    written = engine.apply_refactoring_multi_file(proposal)
    host = written[proposal.file_path]
    assert _line_holding(_helper_source(host), "total += value * 2").endswith("  # noqa: E501")
    assert sum(text.count("# noqa: E501") for text in written.values()) == 1


def test_a_file_directive_does_not_move_to_another_module(tmp_path: Path) -> None:
    paths = _package(tmp_path / "project", "  # flake8: noqa")
    engine = _engine(cross_module_helpers=True)
    assert not engine.analyze_files([str(path) for path in paths], progress="none")
    assert "directive_outlives_block" in engine.declined_pairs


def test_a_file_directive_moves_within_its_module(tmp_path: Path) -> None:
    path = _write(tmp_path, _LOOP.format(comment="  # flake8: noqa"))
    result = _refactor(path)
    assert _line_holding(_helper_source(result), "total += value * 2").endswith("# flake8: noqa")


def test_every_clustered_site_must_carry_the_directives(tmp_path: Path) -> None:
    source = textwrap.dedent(_LOOP.format(comment="  # type: ignore[operator]")) + textwrap.dedent(
        """

        def third(values):
            total = 0
            for value in values:
                total += value * 2  # type: ignore[operator]
            return total + 1


        def fourth(values):
            total = 0
            for value in values:
                total += value * 2
            return total + 1
        """
    )
    path = _write(tmp_path, source)
    engine = _engine()
    proposals = engine.analyze_file(str(path))
    sites = max(len(proposal.replacements) for proposal in proposals)
    assert sites == 3
    result = engine.apply_refactoring(str(path), proposals[0])
    assert result.count("# type: ignore[operator]") == 1
    assert "def fourth(values):\n    total = 0\n    for value in values:" in result


def test_a_method_helper_carries_comments_at_its_class_indentation(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        class Totals:
            def first(self, values):
                total = self.start
                for value in values:
                    # doubled on purpose
                    total += value * 2  # type: ignore[operator]
                return total + 1

            def second(self, values):
                total = self.start
                for value in values:
                    # doubled on purpose
                    total += value * 2  # type: ignore[operator]
                return total + 1
        """,
    )
    result = _refactor(path)
    helper = _helper_source(result)
    assert "            # doubled on purpose\n            total += value * 2  # type: ignore" in (
        helper
    ), helper
    compile(result, str(path), "exec")
    _assert_same_refactoring_without_comments(path, result)


@requires_mypy
def test_an_ignore_the_checker_needs_lets_the_helper_type_check(tmp_path: Path) -> None:
    """packaging's ``_from_dict``: without its ignore no signature makes the helper check."""
    from towel.type_inference import CheckSuccess, MypyInferrer

    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    path = _write(
        tmp_path,
        """
        from typing import Any, Mapping, Optional, TypeVar

        T = TypeVar("T")


        def _get(d: Mapping[str, Any], expected: type[T], key: str) -> Optional[T]:
            value = d.get(key)
            if value is None or isinstance(value, expected):
                return value
            raise TypeError(key)


        def first(d: Mapping[str, Any]) -> int:
            hashes = _get(d, Mapping, "hashes")  # type: ignore[type-abstract]
            count = len(hashes or {})
            count += 1
            return count


        def second(d: Mapping[str, Any]) -> int:
            hashes = _get(d, Mapping, "hashes")  # type: ignore[type-abstract]
            count = len(hashes or {})
            count += 1
            return count
        """,
    )
    oracle = MypyInferrer()
    try:
        assert oracle.check(str(path), path.read_text()) == CheckSuccess()
        engine = _engine(type_oracle=oracle, reuse_existing_functions=False)
        proposals = engine.analyze_file(str(path))
        assert proposals, engine.declined_pairs
        result = engine.apply_refactoring(str(path), proposals[0])
        assert oracle.check(str(path), result) == CheckSuccess(), result
    finally:
        oracle.close()
    helper = _helper_source(result)
    assert _line_holding(helper, "_get(d, Mapping").endswith("  # type: ignore[type-abstract]")
    assert "d: Mapping[str, Any]" in helper, helper
    # Checked again by the command line, strictly, with unused ignores reported.
    checked_copy = tmp_path / "checked"
    checked_copy.mkdir()
    (checked_copy / "m.py").write_text(result, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--warn-unused-ignores", "m.py"],
        capture_output=True,
        text=True,
        cwd=checked_copy,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_a_helper_inside_the_enclosing_function_carries_its_comments(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        def outer(values, scale):
            def first():
                total = 0
                for value in values:
                    # scaled on purpose
                    total += value * scale  # type: ignore[operator]
                return total

            def second():
                total = 0
                for value in values:
                    # scaled on purpose
                    total += value * scale  # type: ignore[operator]
                return total

            return first() + second()
        """,
    )
    engine = _engine()
    proposals = engine.analyze_file(str(path))
    assert proposals, engine.declined_pairs
    result = engine.apply_refactoring(str(path), proposals[0])
    compile(result, str(path), "exec")
    helper = _helper_source(result)
    lines = helper.split("\n")
    comment = next(index for index, line in enumerate(lines) if "# scaled on purpose" in line)
    assert lines[comment + 1].strip() == "total += value * scale  # type: ignore[operator]"
    assert len(lines[comment]) - len(lines[comment].lstrip()) == len(lines[comment + 1]) - len(
        lines[comment + 1].lstrip()
    )
    assert result.count("# scaled on purpose") == 1
    _assert_same_refactoring_without_comments(path, result)


_OVER_ARGUMENT = """
    def first(record, alpha):
        total = 0
        total += len({first}){comment}
        total = total * 2
        return total


    def second(record, beta):
        total = 0
        total += len({second}){comment}
        total = total * 2
        return total
    """

_TOOL_DIRECTIVES = [
    "# noqa: E501",
    "# pragma: no cover",
    "# nosec B101",
    "# pylint: disable=no-member",
    "# fmt: skip",
]


@pytest.mark.parametrize("directive", _TOOL_DIRECTIVES)
def test_a_directive_over_code_that_becomes_an_argument_declines_the_pair(
    tmp_path: Path, directive: str
) -> None:
    """The differing code would be written at the call site, where no tool sees the directive."""
    path = _write(
        tmp_path,
        _OVER_ARGUMENT.format(
            first="record.alpha.items()", second="record.beta.items()", comment="  " + directive
        ),
    )
    assert "directive_on_argument" in _declined(path)


@pytest.mark.parametrize("argument", ["name", "literal"])
@pytest.mark.parametrize("directive", _TOOL_DIRECTIVES)
def test_a_directive_over_a_name_or_literal_argument_moves(
    tmp_path: Path, directive: str, argument: str
) -> None:
    """A name or a literal at the call site is nothing a tool reports on."""
    first, second = ("alpha", "beta") if argument == "name" else ("'alpha'", "'beta'")
    path = _write(
        tmp_path, _OVER_ARGUMENT.format(first=first, second=second, comment="  " + directive)
    )
    result = _refactor(path)
    assert _line_holding(_helper_source(result), "total += len(").endswith("  " + directive)
    assert result.count(directive) == 1
    _assert_same_refactoring_without_comments(path, result)


def test_a_pragma_on_a_clause_reaches_the_arguments_inside_it(tmp_path: Path) -> None:
    """Coverage excludes the whole clause a pragma's line opens, arguments included."""
    path = _write(
        tmp_path,
        """
        def first(record):
            total = 0
            if total == 0:  # pragma: no cover
                total += len(record.alpha.items())
            return total * 2


        def second(record):
            total = 0
            if total == 0:  # pragma: no cover
                total += len(record.beta.items())
            return total * 2
        """,
    )
    assert "directive_on_argument" in _declined(path)


_INSIDE = """
    {prelude}
    def first(values, error):{first_header_comment}
        total = len(values) * 2
        {first_opener}{opener_comment}
            {disable}
            message = "bad: " + str(values){start_comment}
            values.clear(){start_comment}
            raise ValueError(message){start_comment}
        return total + 1


    def second(values, error):{second_header_comment}
        total = sum(values) - 3
        {second_opener}{opener_comment}
            {disable}
            message = "bad: " + str(values){start_comment}
            values.clear(){start_comment}
            raise ValueError(message){start_comment}
        return total * 5
    """


def _inside(**parts: str) -> str:
    defaults = {
        "prelude": "",
        "first_header_comment": "",
        "second_header_comment": "",
        "first_opener": "for item in values:",
        "second_opener": "while error:",
        "opener_comment": "",
        "disable": "pass",
        "start_comment": "",
    }
    defaults.update(parts)
    return _INSIDE.format(**defaults)


@pytest.mark.parametrize(
    "parts",
    [
        # coverage excludes the loop; the helper holding its body would not be
        {"opener_comment": "  # pragma: no cover"},
        # pylint's disable on a def line covers the function's body
        {
            "first_header_comment": "  # pylint: disable=protected-access",
            "second_header_comment": "  # pylint: disable=protected-access",
        },
        # an else clause excluded from coverage
        {
            "first_opener": "if values:\n            pass\n        else:",
            "second_opener": "if error:\n            pass\n        else:",
            "opener_comment": "  # pragma: no cover",
        },
    ],
)
def test_a_directive_around_the_block_declines_the_pair(
    tmp_path: Path, parts: Dict[str, str]
) -> None:
    """The helper would be written outside the clause or function the directive governs."""
    assert "directive_around_block" in _declined(tmp_path / "m.py", _inside(**parts))


def test_a_pylint_disable_earlier_in_the_body_declines_until_it_is_enabled(
    tmp_path: Path,
) -> None:
    disabled = _inside(disable="# pylint: disable=broad-exception-raised")
    assert "directive_around_block" in _declined(tmp_path / "m.py", disabled)
    enabled = _inside(
        disable=(
            "# pylint: disable=broad-exception-raised\n"
            "            # pylint: enable=broad-exception-raised"
        )
    )
    path = _write(tmp_path, enabled, "enabled.py")
    assert _refactor(path)


def test_a_module_wide_disable_reaches_the_helper_too(tmp_path: Path) -> None:
    path = _write(tmp_path, _inside(prelude="# pylint: disable=broad-exception-raised"))
    result = _refactor(path)
    assert result.startswith("# pylint: disable=broad-exception-raised")


def test_a_block_starting_with_an_excluded_statement_declines_the_pair(tmp_path: Path) -> None:
    """The call replacing the block would run and be measured exactly where it did not."""
    source = _inside(disable="pass  # pragma: no cover", start_comment="  # pragma: no cover")
    assert "excluded_block_start" in _declined(tmp_path / "m.py", source)


def test_a_block_opening_with_an_excluded_clause_still_moves(tmp_path: Path) -> None:
    """A header runs whenever it is reached, and so would the call that replaces it."""
    path = _write(
        tmp_path,
        """
        def first(values, error):
            total = len(values) * 2
            if error:  # pragma: no cover
                message = "bad: " + str(values)
                values.clear()
                raise ValueError(message)
            return total + 1


        def second(values, error):
            total = sum(values) - 3
            if error:  # pragma: no cover
                message = "bad: " + str(values)
                values.clear()
                raise ValueError(message)
            return total * 5
        """,
    )
    result = _refactor(path)
    assert _line_holding(_helper_source(result), "if error:").endswith("  # pragma: no cover")


def test_a_class_wide_disable_still_reaches_a_method_helper(tmp_path: Path) -> None:
    """fastjsonschema's generators: the helper is written at the end of the disabled class."""
    path = _write(
        tmp_path,
        """
        class Generator:
            # pylint: disable=line-too-long
            def first(self, value):
                count = self.count + 1
                self.count = count
                self.emit("first", value, count)
                return count

            def second(self, value):
                count = self.count + 1
                self.count = count
                self.emit("second", value, count)
                return count
        """,
    )
    result = _refactor(path)
    tree = ast.parse(result)
    (cls,) = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert any("extracted_func" in getattr(node, "name", "") for node in cls.body), result


def test_functions_under_an_excluded_main_block_keep_their_code(tmp_path: Path) -> None:
    """pygments' builtins scripts: a module helper would be measured though the block is not."""
    path = _write(
        tmp_path,
        """
        if __name__ == "__main__":  # pragma: no cover
            def first(values):
                total = 0
                for value in values:
                    total += value * 2
                return total + 1

            def second(values):
                total = 0
                for value in values:
                    total += value * 2
                return total + 1
        """,
    )
    assert "directive_around_block" in _declined(path)


def test_one_excluded_site_among_measured_ones_still_moves(tmp_path: Path) -> None:
    """markdown's inline patterns: the helper runs from the measured site as that code did."""
    path = _write(
        tmp_path,
        """
        class Old:  # pragma: no cover
            def render(self, value):
                count = self.count + 1
                self.count = count
                self.emit("old", value, count)
                return count


        class New:
            def render(self, value):
                count = self.count + 1
                self.count = count
                self.emit("new", value, count)
                return count
        """,
    )
    assert _refactor(path)
