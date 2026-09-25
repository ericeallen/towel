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

"""The import sorter touches only what the project's own sorter would, and only Towel's imports.

An import runs its module where it stands, so reordering a file's own imports
reorders their import-time effects: ``from pkg import zeta`` then ``from pkg
import alpha`` registers zeta's plugin first. Towel's finisher therefore sorts
a file only when the configured tool selects it (ruff's ``exclude``,
``extend-exclude``, ``lint.exclude`` and ``per-file-ignores``; isort's
``skip``, ``extend_skip``, ``skip_glob`` and ``extend_skip_glob``), only when
the tool already leaves the file's text before the change as it is, and keeps
the result only when the file's own imports stay in their order. The same
file selection governs the formatters: ruff's through ``--force-exclude``,
Black's ``exclude``, ``extend-exclude`` and ``force-exclude``.

Each case makes at most a couple of real tool calls on a few lines of text.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pytest

from towel.formatting import (
    SortOutcome,
    black_excludes,
    formatter_for_project,
    import_sorter_for_project,
    sort_added_imports,
    sorted_where_already_sorted,
)

# Before the change the file imports ``sys``; Towel's change adds ``import os``.
ORIGINAL = "import sys\n\nprint(sys.argv)\n"
ASSEMBLED = "import sys\nimport os\n\nprint(sys.argv, os.sep)\n"
SORTED = "import os\nimport sys\n\nprint(sys.argv, os.sep)\n"


def _project(root: Path, pyproject: str, relative: str, original: str = ORIGINAL) -> Path:
    (root / "pyproject.toml").write_text(pyproject)
    module = root / relative
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(original)
    return module


def _finish(module: Path, assembled: str = ASSEMBLED, project: Optional[Path] = None) -> str:
    finisher = import_sorter_for_project(project or module).tool
    assert finisher is not None
    return finisher(str(module), assembled)


# -- the configured tool's own file selection -----------------------------------------


RUFF_SELECTION = {
    "exclude": '[tool.ruff]\nexclude = ["pkg/app.py"]\n[tool.ruff.lint]\nselect = ["I"]\n',
    "exclude-directory": (
        '[tool.ruff]\nexclude = ["pkg/_sync"]\n[tool.ruff.lint]\nselect = ["I"]\n'
    ),
    "extend-exclude": (
        '[tool.ruff]\nextend-exclude = ["pkg/app.py"]\n[tool.ruff.lint]\nselect = ["I"]\n'
    ),
    "lint.exclude": '[tool.ruff.lint]\nselect = ["I"]\nexclude = ["pkg/app.py"]\n',
    "per-file-ignores": (
        '[tool.ruff.lint]\nselect = ["I"]\n'
        '[tool.ruff.lint.per-file-ignores]\n"pkg/app.py" = ["I001"]\n'
    ),
}


@pytest.mark.parametrize("form", sorted(RUFF_SELECTION))
def test_ruff_leaves_a_file_its_configuration_excludes(tmp_path: Path, form: str) -> None:
    pytest.importorskip("ruff")
    relative = "pkg/_sync/app.py" if form == "exclude-directory" else "pkg/app.py"
    module = _project(tmp_path, RUFF_SELECTION[form], relative)
    assert _finish(module) == ASSEMBLED
    # The same project sorts a file it does not exclude.
    other = tmp_path / "pkg" / "other.py"
    other.write_text(ORIGINAL)
    assert _finish(other) == SORTED


ISORT_SELECTION = {
    "skip-name": ('[tool.isort]\nskip = ["app.py"]\n', "pkg/app.py"),
    "skip-path": ('[tool.isort]\nskip = ["pkg/app.py"]\n', "pkg/app.py"),
    "skip-directory-path": ('[tool.isort]\nskip = ["pkg/_sync"]\n', "pkg/_sync/app.py"),
    "extend_skip": ('[tool.isort]\nextend_skip = ["_sync"]\n', "pkg/_sync/app.py"),
    "skip_glob": ('[tool.isort]\nskip_glob = ["pkg/_sync/*"]\n', "pkg/_sync/app.py"),
    "skip_glob-directory": ('[tool.isort]\nskip_glob = ["*/_sync"]\n', "pkg/_sync/app.py"),
    "extend_skip_glob": (
        '[tool.isort]\nextend_skip_glob = ["pkg/*/app.py"]\n',
        "pkg/_sync/app.py",
    ),
}


@pytest.mark.parametrize("form", sorted(ISORT_SELECTION))
def test_isort_leaves_a_file_its_configuration_skips(tmp_path: Path, form: str) -> None:
    pytest.importorskip("isort")
    pyproject, relative = ISORT_SELECTION[form]
    module = _project(tmp_path, pyproject, relative)
    assert _finish(module) == ASSEMBLED
    other = tmp_path / "pkg" / "other.py"
    other.write_text(ORIGINAL)
    assert _finish(other) == SORTED


def _staged(root: Path, module: Path) -> Path:
    """A copy of the project at ``root`` as a run stages it, and ``module``'s place in it."""
    stage = root.parent / "towel-stage-copy" / root.name
    for source in (root / "pyproject.toml", module):
        copy = stage / source.relative_to(root)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(source.read_bytes())
    return stage / module.relative_to(root)


@pytest.mark.parametrize(
    "pyproject",
    [
        '[tool.isort]\nskip_glob = ["pkg/_sync/*"]\n',
        '[tool.ruff]\nexclude = ["pkg/_sync"]\n[tool.ruff.lint]\nselect = ["I"]\n',
    ],
    ids=["isort", "ruff"],
)
def test_a_staged_copy_is_judged_as_the_project_file_it_copies(
    tmp_path: Path, pyproject: str
) -> None:
    pytest.importorskip("isort" if "isort" in pyproject else "ruff")
    root = tmp_path / "project"
    root.mkdir()
    module = _project(root, pyproject, "pkg/_sync/app.py")
    # The finisher is chosen for the project and called on the stage's copy.
    assert _finish(_staged(root, module), project=root) == ASSEMBLED


def test_ruff_format_leaves_a_snippet_for_an_excluded_path_unformatted(tmp_path: Path) -> None:
    pytest.importorskip("ruff")
    module = _project(tmp_path, '[tool.ruff]\nextend-exclude = ["pkg/_sync"]\n', "pkg/_sync/a.py")
    formatter = formatter_for_project(module).tool
    assert formatter is not None
    assert formatter("value=f( 'x' )") == "value=f( 'x' )"
    included = formatter_for_project(tmp_path / "pkg").tool
    assert included is not None and included("value=f( 'x' )") == 'value = f("x")'


@pytest.mark.parametrize(
    "black, excluded",
    [
        ('exclude = "/_sync/"', True),
        ('extend-exclude = "pkg/_sync"', True),
        ('force-exclude = "app\\\\.py$"', True),
        ('extend-exclude = """\n(\n  ^/pkg/_sync/\n)\n"""', True),
        ('extend-exclude = "other"', False),
        ("line-length = 100", False),
    ],
)
def test_black_leaves_a_path_its_configuration_excludes(
    tmp_path: Path, black: str, excluded: bool
) -> None:
    pytest.importorskip("black")
    module = _project(tmp_path, f"[tool.black]\n{black}\n", "pkg/_sync/app.py")
    assert black_excludes(module) is excluded
    formatter = formatter_for_project(module).tool
    assert formatter is not None
    assert (formatter("value=f( 'x' )") == "value=f( 'x' )") is excluded


def test_black_default_exclusions_hold_when_the_project_sets_none(tmp_path: Path) -> None:
    pytest.importorskip("black")
    module = _project(tmp_path, "[tool.black]\nline-length = 100\n", "build/lib/app.py")
    assert black_excludes(module)
    assert not black_excludes(_project(tmp_path, "[tool.black]\n", "pkg/app.py"))


# -- only a file the sorter already leaves as it is ------------------------------------

ISORT_PROJECT = '[tool.isort]\nprofile = "black"\n'


@pytest.mark.parametrize("tool", ["ruff", "isort"])
def test_a_file_whose_own_imports_are_unsorted_is_not_sorted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tool: str
) -> None:
    pytest.importorskip(tool)
    pyproject = '[tool.ruff.lint]\nselect = ["I"]\n' if tool == "ruff" else ISORT_PROJECT
    # ``zeta`` loads before ``alpha`` on purpose; Towel adds ``import os``.
    original = "from pkg import zeta\nfrom pkg import alpha\n\nprint(zeta, alpha)\n"
    assembled = "from pkg import zeta\nfrom pkg import alpha\nimport os\n\nprint(zeta, alpha, os)\n"
    module = _project(tmp_path, pyproject, "m.py", original)
    finisher = import_sorter_for_project(module).tool
    assert finisher is not None
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert finisher(str(module), assembled) == assembled
        assert finisher(str(module), assembled) == assembled
    reports = [record.getMessage() for record in caplog.records]
    assert len(reports) == 1, "a file is reported once"
    assert "would change the imports this file already has" in reports[0]


@pytest.mark.parametrize("tool", ["ruff", "isort"])
def test_a_sorted_file_has_towels_import_placed_and_nothing_else_moved(
    tmp_path: Path, tool: str
) -> None:
    pytest.importorskip(tool)
    pyproject = '[tool.ruff.lint]\nselect = ["I"]\n' if tool == "ruff" else ISORT_PROJECT
    original = (
        "import os\nimport sys\nfrom typing import List\n\nx: List[str] = [os.sep, sys.path[0]]\n"
    )
    assembled = (
        "import os\nimport sys\nfrom typing import List\nimport abc\nfrom typing import Any\n\n"
        "x: List[str] = [os.sep, sys.path[0]]\n"
    )
    module = _project(tmp_path, pyproject, "m.py", original)
    finished = _finish(module, assembled)
    assert finished == (
        "import abc\nimport os\nimport sys\nfrom typing import Any, List\n\n"
        "x: List[str] = [os.sep, sys.path[0]]\n"
    )


# -- the file's own imports keep their order, or the sort is not used ------------------


def _scripted(results: Dict[str, str]) -> Tuple[Callable[[str, str], Optional[str]], List[str]]:
    """A sorter that answers from ``results`` (else leaves the text), and the texts it was asked."""
    asked: List[str] = []

    def sort(path: str, source: str) -> Optional[str]:
        asked.append(source)
        return results.get(source, source)

    return sort, asked


def test_a_sort_that_moves_the_files_own_imports_is_discarded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = "from pkg import zeta\nfrom pkg import alpha\n\nprint(zeta, alpha)\n"
    assembled = "from pkg import zeta\nfrom pkg import alpha\nimport os\n\nprint(zeta, alpha, os)\n"
    # A sorter that leaves the original alone yet merges and reorders its
    # imports once Towel's is added: alpha's module would now run first.
    merged = "import os\nfrom pkg import alpha, zeta\n\nprint(zeta, alpha, os)\n"
    sort, _ = _scripted({assembled: merged})
    result = sort_added_imports(sort, "m.py", original, assembled)
    assert (result.text, result.outcome) == (assembled, SortOutcome.MOVED_EXISTING)
    module = tmp_path / "m.py"
    module.write_text(original)
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert sorted_where_already_sorted(sort, "a sorter")(str(module), assembled) == assembled
    assert "moved imports this file already had" in caplog.text


def test_a_sort_that_only_moves_towels_import_is_kept() -> None:
    original = "import sys\nfrom typing import List\n\nprint(sys, List)\n"
    assembled = "import sys\nfrom typing import List\nfrom typing import Any\n\nprint(sys, List)\n"
    finished = "import sys\nfrom typing import Any, List\n\nprint(sys, List)\n"
    sort, _ = _scripted({assembled: finished})
    result = sort_added_imports(sort, "m.py", original, assembled)
    assert (result.text, result.outcome) == (finished, SortOutcome.SORTED)


def test_a_sort_that_changes_more_than_import_order_is_discarded() -> None:
    sort, _ = _scripted({ASSEMBLED: SORTED.replace("os.sep", "os.pathsep")})
    result = sort_added_imports(sort, "m.py", ORIGINAL, ASSEMBLED)
    assert (result.text, result.outcome) == (ASSEMBLED, SortOutcome.NOT_A_PERMUTATION)


def test_a_failed_sort_leaves_the_text_as_assembled() -> None:
    result = sort_added_imports(lambda path, source: None, "m.py", ORIGINAL, ASSEMBLED)
    assert (result.text, result.outcome) == (ASSEMBLED, SortOutcome.TOOL_FAILED)


def test_the_original_is_judged_once_for_every_variant_of_a_change(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text(ORIGINAL)
    sort, asked = _scripted({ASSEMBLED: SORTED})
    finisher = sorted_where_already_sorted(sort, "a sorter")
    variant = ASSEMBLED.replace("os.sep", "os.sep, 1")
    assert finisher(str(module), ASSEMBLED) == SORTED
    assert finisher(str(module), variant) == variant  # the scripted sorter leaves it
    assert asked == [ORIGINAL, ASSEMBLED, variant]


def test_an_unreadable_original_is_not_sorted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sort, asked = _scripted({ASSEMBLED: SORTED})
    with caplog.at_level(logging.WARNING, logger="towel"):
        finished = sorted_where_already_sorted(sort, "a sorter")(
            str(tmp_path / "gone.py"), ASSEMBLED
        )
    assert (finished, asked) == (ASSEMBLED, [])
    assert "could not be read" in caplog.text
