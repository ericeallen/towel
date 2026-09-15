"""The pre-run scan names modules that observe frames, tracebacks, or source."""

from __future__ import annotations

import contextlib
import io

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import frame_sensitivity_markers


def test_markers_detect_each_construct() -> None:
    assert "stacklevel-warning" in frame_sensitivity_markers(
        "import warnings\nwarnings.warn('x', stacklevel=3)\n"
    )
    assert "frame" in frame_sensitivity_markers("import sys\nf = sys._getframe(1)\n")
    assert "frame" in frame_sensitivity_markers("err = e\nc = err.__traceback__.tb_frame\n")
    assert "traceback" in frame_sensitivity_markers("t = exc.__traceback__\n")
    assert "source" in frame_sensitivity_markers("import inspect\ns = inspect.getsource(f)\n")


def test_plain_code_and_plain_warnings_are_not_flagged() -> None:
    assert frame_sensitivity_markers("def f(a, b):\n    return a + b\n") == frozenset()
    # A warning without stacklevel does not depend on the call frame.
    assert frame_sensitivity_markers("import warnings\nwarnings.warn('x')\n") == frozenset()


def test_syntax_error_is_silent() -> None:
    assert frame_sensitivity_markers("def (:\n") == frozenset()


def test_directory_refactor_warns_about_a_stacklevel_module(tmp_path, capsys) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "__init__.py").write_text("")
    (project / "warns.py").write_text(
        "import warnings\n\n\ndef check(flag):\n"
        "    if flag:\n        warnings.warn('deprecated', stacklevel=2)\n"
    )
    (project / "plain.py").write_text("def add(a, b):\n    return a + b\n")
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(project), str(project), progress="tqdm")
    err = capsys.readouterr().err
    assert "attribute warnings to a caller's frame" in err
    assert "warns.py" in err
    assert "plain.py" not in err


def test_warning_is_a_diagnostic_shown_even_under_progress_none(tmp_path, capsys) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "__init__.py").write_text("")
    (project / "warns.py").write_text(
        "import warnings\n\n\ndef check(flag):\n"
        "    if flag:\n        warnings.warn('deprecated', stacklevel=2)\n"
    )
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(project), str(project), progress="none")
    assert "caller's frame" in capsys.readouterr().err
