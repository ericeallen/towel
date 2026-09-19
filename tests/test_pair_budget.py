"""The candidate-pair budget and the tuning flags that reach it.

Blocks are quadratic in a function's length and pairs quadratic in blocks, so
a file of many long, similar functions can propose tens of millions of pairs
before the first one is evaluated (the fifth audit reached 28 GB on 8,000
lines). Past the budget the largest buckets of similar blocks are left out,
loudly; under it nothing changes.
"""

from __future__ import annotations

import logging
from pathlib import Path
import textwrap

import pytest

from towel.cli import DryOptions, PreviewOptions, _build_parser
from towel.unification.defaults import (
    DEFAULT_MAX_CANDIDATE_PAIRS,
    DEFAULT_MAX_PARAMETERS,
    DEFAULT_MIN_LINES,
)
from towel.diagnostics import Settings
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine

SERIAL = Settings.from_environ({"TOWEL_WORKERS": "1"})


def _uniform_module() -> str:
    body = "\n".join(f"    v{i} = data[{i}] + {i}" for i in range(8))
    return "\n\n".join(f"def f{n}(data):\n{body}\n    return v7\n" for n in range(4))


def test_under_the_budget_nothing_is_left_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "m.py"
    path.write_text(_uniform_module())
    with caplog.at_level(logging.WARNING, logger="towel"):
        proposals = UnificationRefactorEngine(min_lines=3, settings=SERIAL).analyze_file(str(path))
    assert proposals
    assert "exceed the budget" not in caplog.text


def test_over_the_budget_the_largest_buckets_are_dropped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "m.py"
    path.write_text(_uniform_module())
    functions = collect_functions(analyze_scopes(parse_modules([str(path)])))
    unbounded = UnificationRefactorEngine(min_lines=3, settings=SERIAL).find_block_pairs(
        functions, progress="none"
    )
    with caplog.at_level(logging.WARNING, logger="towel"):
        budgeted = UnificationRefactorEngine(
            min_lines=3, max_candidate_pairs=10, settings=SERIAL
        ).find_block_pairs(functions, progress="none")
    assert "exceed the budget of 10" in caplog.text
    assert len(budgeted) <= 10 < len(unbounded)


def test_a_budget_of_zero_means_no_budget(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "m.py"
    path.write_text(_uniform_module())
    with caplog.at_level(logging.WARNING, logger="towel"):
        UnificationRefactorEngine(min_lines=3, max_candidate_pairs=0, settings=SERIAL).analyze_file(
            str(path)
        )
    assert "exceed the budget" not in caplog.text


def test_the_tuning_flags_reach_the_engine_options() -> None:
    parser = _build_parser()
    dry = DryOptions.from_namespace(
        parser.parse_args(
            ["dry", "in", "out", "--min-lines", "5", "--max-parameters", "2", "--max-pairs", "7"]
        )
    )
    assert (dry.min_lines, dry.max_parameters, dry.max_pairs) == (5, 2, 7)
    preview = PreviewOptions.from_namespace(parser.parse_args(["preview", "in"]))
    assert (preview.min_lines, preview.max_parameters, preview.max_pairs) == (
        DEFAULT_MIN_LINES,
        DEFAULT_MAX_PARAMETERS,
        DEFAULT_MAX_CANDIDATE_PAIRS,
    )


def test_a_negative_tuning_value_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["preview", "in", "--min-lines", "-1"])
    assert "is negative" in capsys.readouterr().err


def test_min_lines_from_the_command_line_changes_what_is_found(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def a(x):
                y = x + 1
                z = y * 2
                return z

            def b(x):
                y = x + 1
                z = y * 2
                return z + 1
            """))
    loose = UnificationRefactorEngine(min_lines=2, settings=SERIAL).analyze_file(str(path))
    strict = UnificationRefactorEngine(min_lines=4, settings=SERIAL).analyze_file(str(path))
    assert loose and not strict
