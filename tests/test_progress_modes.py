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

import contextlib
import io
import logging
from pathlib import Path

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine


def _make_fixture(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    code = """
def f1():
    x = 1
    y = x + 2
    return y

def f2():
    x = 1
    y = x + 2
    return y

def g1():
    for i in range(3):
        q = i * 2
    return q

def g2():
    for i in range(3):
        q = i * 2
    return q
""".strip()
    (dir_path / "sample.py").write_text(code, encoding="utf-8")


def test_detail_progress_lists_proposals(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fixture = tmp_path / "proj"
    _make_fixture(fixture)
    engine = UnificationRefactorEngine(min_lines=3)
    out_dir = tmp_path / "out_dir"  # outside input to avoid recursive copy
    # Detail mode reports through the towel logger at INFO, which the command
    # line routes to stderr; here the records are read directly.
    with caplog.at_level(logging.INFO, logger="towel"), contextlib.redirect_stdout(io.StringIO()):
        results, termination = engine.refactor_directory_to_fixed_point(
            str(fixture), str(out_dir), max_iterations=1, progress="detail"
        )
    messages = [record.getMessage() for record in caplog.records]
    # The fixture holds two duplicate pairs; detail mode announces the count
    # and then lists every discovered proposal, numbered, in discovery order.
    assert "[towel] Discovered 2 proposal(s)" in messages
    listed = [message.strip() for message in messages if message.strip()[:2] in {"1.", "2."}]
    assert listed == [
        "1. Extract common code from f1 and f2",
        "2. Extract common code from g1 and g2",
    ]
    # One iteration applies only the first proposal, so the cap ends the run
    # with the second still pending.
    assert termination == "iteration_cap"
    applied = [description for _, descriptions in results.values() for description in descriptions]
    assert applied == ["Extract common code from f1 and f2"]


def test_termination_reason_fixed_point(tmp_path: Path) -> None:
    fixture = tmp_path / "proj_fixed"
    fixture.mkdir(parents=True, exist_ok=True)
    # Only one duplicate group so fixed point after applying it
    code = """
def a():
    x = 1
    y = x + 2
    return y

def b():
    x = 1
    y = x + 2
    return y
""".strip()
    (fixture / "only.py").write_text(code, encoding="utf-8")
    engine = UnificationRefactorEngine(min_lines=3)
    out_dir = tmp_path / "out_dir_fixed"  # outside input
    results, termination = engine.refactor_directory_to_fixed_point(
        str(fixture), str(out_dir), max_iterations=0, progress="none"
    )
    assert termination == "fixed_point"
    total = sum(c for c, _ in results.values())
    assert total == 1


def _two_round_project(root: Path) -> None:
    """Two useful extractions; rewriting their shared host triggers local re-analysis."""
    root.mkdir()
    prices = (
        "    total = 0\n    for item in items:\n"
        "        base = item * 3\n        tax = base * 0.1\n"
        "        taxed = base + tax\n        discounted = taxed - 2\n"
        "        fee = discounted * 0.02\n        charged = discounted + fee\n"
        "        rounded = (charged * 100) // 1 / 100\n        total += rounded\n"
    )
    names = (
        "    names = []\n    for record in records:\n"
        "        stripped = record.name.strip()\n        lower = stripped.lower()\n"
        "        words = lower.replace('-', ' ')\n        punctuated = words.replace('.', ' ')\n"
        "        spaces = punctuated.replace('  ', ' ')\n        tabs = spaces.replace('\\t', ' ')\n"
        "        cleaned = tabs.strip()\n        names.append(cleaned)\n"
    )
    (root / "a.py").write_text(
        "def alpha(items):\n"
        + prices
        + '    print(total, "alpha")\n    return total\n\n'
        + "def alpha_two(records):\n"
        + names
        + '    print(names, "alpha_two")\n    return names\n'
    )
    # Two top-level modules share a helper only when the borrower imports the host.
    (root / "b.py").write_text(
        "import a\n\ndef beta(items):\n" + prices + '    print(total, "beta")\n    return total\n'
    )
    (root / "c.py").write_text(
        "import a\n\ndef gamma(records):\n"
        + names
        + '    print(names, "gamma")\n    return names\n'
    )


def test_none_is_silent_through_every_re_analysis(tmp_path: Path) -> None:
    _two_round_project(tmp_path / "proj")
    engine = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, parameterize_builtins=True
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(tmp_path / "proj"), str(tmp_path / "out"), max_iterations=0, progress="none"
        )
    assert reason == "fixed_point"
    assert sum(count for count, _ in results.values()) == 4
    assert out.getvalue() == "" and err.getvalue() == ""


def test_none_is_silent_for_a_single_file(tmp_path: Path) -> None:
    _make_fixture(tmp_path / "proj")
    engine = UnificationRefactorEngine(min_lines=3)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        _, applied, _ = engine.refactor_to_fixed_point(
            str(tmp_path / "proj" / "sample.py"), progress="none"
        )
    assert applied > 0
    assert out.getvalue() == "" and err.getvalue() == ""


def test_drivers_run_to_a_fixed_point_by_default(tmp_path: Path) -> None:
    _two_round_project(tmp_path / "proj")
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _, reason = engine.refactor_directory_to_fixed_point(
            str(tmp_path / "proj"), str(tmp_path / "out"), progress="none"
        )
    assert reason == "fixed_point"


def test_preview_honours_progress_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from towel.cli import main

    _two_round_project(tmp_path / "proj")
    monkeypatch.setattr(
        sys, "argv", ["towel", "preview", str(tmp_path / "proj"), "--progress", "none"]
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        main()
    assert "Would apply" in out.getvalue()
    # No bar of either kind; the file count is an ordinary log line, not progress.
    assert (
        "it/s" not in err.getvalue() and "%|" not in err.getvalue() and "\r" not in err.getvalue()
    )


def _without_tqdm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tqdm look uninstalled, as in a plain ``pip install code-towel``, and the note unsaid."""
    import sys

    from towel.unification import fixed_point

    monkeypatch.setitem(sys.modules, "tqdm.auto", None)
    monkeypatch.setattr(fixed_point, "_TQDM_NOTED", False)


def test_default_progress_without_tqdm_draws_every_phase_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The apply phase, where verification spends the time, falls back as analysis does.

    The note once said only the pairing phase drew an inline bar; analysis and
    unification drew one too, and the apply phase alone drew nothing.
    """
    _make_fixture(tmp_path / "proj")
    _without_tqdm(monkeypatch)
    err = io.StringIO()
    with (
        caplog.at_level(logging.WARNING, logger="towel"),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(err),
    ):
        results, _ = UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
            str(tmp_path / "proj"), str(tmp_path / "out"), progress="tqdm"
        )
    assert sum(count for count, _ in results.values()) == 2
    for label in ("Analyzing files:", "Pairing blocks:", "[towel] apply ", "[towel] applied "):
        assert label in err.getvalue(), (label, err.getvalue())
    notes = [record.getMessage() for record in caplog.records if "tqdm" in record.getMessage()]
    assert notes == ["tqdm is not installed; progress is shown as inline bars on stderr instead"]


@pytest.mark.parametrize("mode", ["none", "detail"])
def test_modes_without_a_bar_draw_no_inline_apply_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _make_fixture(tmp_path / "proj")
    _without_tqdm(monkeypatch)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
            str(tmp_path / "proj"), str(tmp_path / "out"), progress=mode  # type: ignore[arg-type]
        )
    assert "\r" not in err.getvalue()


def test_detail_progress_logs_each_pass_of_a_single_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``--progress detail`` promised dry and preview a log of each pass; a file run logged none."""
    _make_fixture(tmp_path / "proj")
    err = io.StringIO()
    with (
        caplog.at_level(logging.INFO, logger="towel"),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(err),
    ):
        _, applied, _ = UnificationRefactorEngine(min_lines=3).refactor_to_fixed_point(
            str(tmp_path / "proj" / "sample.py"),
            progress="detail",
            output_path=str(tmp_path / "out.py"),
        )
    assert applied == 2
    messages = [record.getMessage() for record in caplog.records]
    # One pass per application, and the last finds nothing left.
    assert messages.count("[towel] Discovered 2 proposal(s)") == 1
    assert messages.count("[towel] Discovered 1 proposal(s)") == 1
    listed = [message.strip() for message in messages if message.strip()[:2] in {"1.", "2."}]
    assert listed[:2] == [
        "1. Extract common code from f1 and f2",
        "2. Extract common code from g1 and g2",
    ]
    assert "\r" not in err.getvalue(), "detail logs instead of drawing a bar"


@pytest.mark.parametrize("command", ["dry", "preview"])
def test_cli_detail_progress_logs_a_single_file_run(tmp_path: Path, command: str) -> None:
    from tests.test_cli_integration import invoke

    _make_fixture(tmp_path / "proj")
    source = str(tmp_path / "proj" / "sample.py")
    target = (
        [source, str(tmp_path / "out.py"), "--no-interactive"] if command == "dry" else [source]
    )
    result = invoke([command, *target, "--no-types", "--min-lines", "3", "--progress", "detail"])
    assert result.status == 0, result.stderr
    assert "[towel] Discovered 2 proposal(s)" in result.stderr, result.stderr
