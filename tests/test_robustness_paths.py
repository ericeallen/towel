"""Error paths the third audit found unguarded: each now declines, warns, or reports."""

from __future__ import annotations

import builtins
import logging
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from towel import formatting
from towel.changes import ChangeConflict, recover
from towel.cli import _confirm, _find_extracted_helpers, helper_inventory
from towel.diagnostics import Settings
from towel.formatting import FormattingChangedCode, checked, import_sorter_for_project
from towel.type_inference import CheckFailure, PyrightOracle
from towel.unification.refactor_engine import UnificationRefactorEngine


def test_confirm_declines_at_a_closed_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", closed)
    assert _confirm("Proceed? ") is False
    monkeypatch.setattr(builtins, "input", lambda prompt: " Y ")
    assert _confirm("Proceed? ") is True


def test_helper_inventory_ignores_a_symlinked_module(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text(
        "def __extracted_func_0(__param_0):\n    return __param_0 + 1\n\n"
        "def run(x):\n    return __extracted_func_0(x)\n"
    )
    (pkg / "link.py").symlink_to(pkg / "a.py")
    helpers = _find_extracted_helpers(tmp_path, None, None)
    assert [path.name for path, _, _, _ in helpers] == ["a.py"]
    inventory = helper_inventory(tmp_path, helpers)
    assert [entry["name"] for entry in inventory["helpers"]] == ["__extracted_func_0"]


def test_isort_skip_settings_leave_the_file_as_assembled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("isort")
    (tmp_path / "pyproject.toml").write_text('[tool.isort]\nskip = ["m.py"]\n')
    sorter = import_sorter_for_project(tmp_path).tool
    assert sorter is not None
    # Each file held ``import sys``; Towel added ``import os`` after it.
    source = "import sys\nimport os\n"
    for name, text in (("m.py", "import sys\n"), ("n.py", "# isort: skip_file\nimport sys\n")):
        (tmp_path / name).write_text(text)
    (tmp_path / "o.py").write_text("import sys\n")
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert sorter(str(tmp_path / "m.py"), source) == source
        skip_file = "# isort: skip_file\n" + source
        assert sorter(str(tmp_path / "n.py"), skip_file) == skip_file
        assert sorter(str(tmp_path / "o.py"), source) == "import os\nimport sys\n"
    # The project told isort to leave these files alone; that is no failure.
    assert caplog.text == ""


def _completed(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    "stdout, fragment",
    [
        ("[1, 2]", "no JSON"),
        ('{"generalDiagnostics": "x"}', "unexpected shape"),
        ("nope", "no JSON"),
    ],
)
def test_pyright_output_of_the_wrong_shape_infers_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stdout: str,
    fragment: str,
) -> None:
    module = tmp_path / "m.py"
    module.write_text("x = 1\n")
    oracle = PyrightOracle.__new__(PyrightOracle)
    oracle._command = ["pyright"]
    oracle._server = None
    oracle._warmed = {}
    oracle._probe_copies = {}
    oracle._interpreter = sys.executable
    oracle._search_path = ()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout))
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert isinstance(oracle._diagnostics(str(module), "x = 1\n"), CheckFailure)
    oracle.close()
    assert fragment in caplog.text


def test_a_hung_pyright_is_abandoned_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    module = tmp_path / "m.py"
    module.write_text("x = 1\n")
    oracle = PyrightOracle.__new__(PyrightOracle)
    oracle._command = ["pyright"]
    oracle._server = None
    oracle._warmed = {}
    oracle._probe_copies = {}
    oracle._interpreter = sys.executable
    oracle._search_path = ()

    def hang(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="pyright", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert isinstance(oracle._diagnostics(str(module), "x = 1\n"), CheckFailure)
    oracle.close()
    assert "timed out" in caplog.text


def test_ruff_import_sorting_failures_are_reported_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I"]\n')
    monkeypatch.setattr(formatting, "_ruff_executable", lambda: ["ruff"])
    sorter = import_sorter_for_project(tmp_path).tool
    assert sorter is not None
    source = "import sys\nimport os\n"
    (tmp_path / "m.py").write_text("import sys\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("", 1, "boom"))
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert sorter(str(tmp_path / "m.py"), source) == source
    assert "boom" in caplog.text

    def hang(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="ruff", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert sorter(str(tmp_path / "m.py"), source) == source
    assert "timed out" in caplog.text


def test_a_formatter_that_returns_garbage_is_reported_as_changed_code() -> None:
    with pytest.raises(FormattingChangedCode, match="unparsable"):
        checked(lambda source: "def (:\n")("x = 1\n")


def test_a_misspelt_worker_count_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert Settings.from_environ({"TOWEL_WORKERS": "many"}).workers == 1
    assert "TOWEL_WORKERS" in caplog.text
    assert Settings.from_environ({"TOWEL_WORKERS": "3"}).workers == 3
    assert Settings.from_environ({}).workers is None


def test_a_configured_worker_count_never_reaches_fork_where_there_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = UnificationRefactorEngine(settings=Settings.from_environ({"TOWEL_WORKERS": "4"}))
    monkeypatch.setattr(multiprocessing, "get_all_start_methods", lambda: ["spawn"])
    assert engine._parallel_workers() == 1


def test_a_corrupt_manifest_is_a_change_conflict(tmp_path: Path) -> None:
    journal = tmp_path / ".towel-transaction-active"
    journal.mkdir(mode=0o700)
    (journal / "manifest.json").write_text("{not json")
    with pytest.raises(ChangeConflict, match="Invalid transaction manifest"):
        recover(journal)


def test_invalidating_a_path_forgets_its_cached_lines(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text("x = 1\n")
    engine = UnificationRefactorEngine()
    assert engine._source_lines(str(module)) == ("x = 1\n",)
    # Rewrite the file with the same size and modification time, which the
    # memo's stat check cannot tell from the original: the stale lines are
    # served until the path is invalidated, and the new ones after.
    stat = module.stat()
    module.write_text("x = 2\n")
    os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert engine._source_lines(str(module)) == ("x = 1\n",)
    engine.invalidate_paths([str(module)])
    assert engine._source_lines(str(module)) == ("x = 2\n",)
