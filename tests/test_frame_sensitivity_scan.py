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

"""The pre-run scan names modules that observe frames, tracebacks, or source."""

from __future__ import annotations

import contextlib
import logging
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


def test_directory_refactor_warns_about_a_stacklevel_module(tmp_path, caplog) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "__init__.py").write_text("")
    (project / "warns.py").write_text(
        "import warnings\n\n\ndef check(flag):\n"
        "    if flag:\n        warnings.warn('deprecated', stacklevel=2)\n"
    )
    (project / "plain.py").write_text("def add(a, b):\n    return a + b\n")
    engine = UnificationRefactorEngine(min_lines=3)
    with (
        caplog.at_level(logging.WARNING, logger="towel"),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        engine.refactor_directory_to_fixed_point(str(project), str(project), progress="tqdm")
    err = "\n".join(record.getMessage() for record in caplog.records)
    assert "attribute warnings to a caller's frame" in err
    assert "warns.py" in err
    assert "plain.py" not in err


def test_warning_is_a_diagnostic_shown_even_under_progress_none(tmp_path, caplog) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "__init__.py").write_text("")
    (project / "warns.py").write_text(
        "import warnings\n\n\ndef check(flag):\n"
        "    if flag:\n        warnings.warn('deprecated', stacklevel=2)\n"
    )
    engine = UnificationRefactorEngine(min_lines=3)
    with (
        caplog.at_level(logging.WARNING, logger="towel"),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        engine.refactor_directory_to_fixed_point(str(project), str(project), progress="none")
    warnings_logged = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("caller's frame" in r.getMessage() for r in warnings_logged)
