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

"""A file the program's names reach two ways is in doubt, however the aliasing arises.

The round-4 audit's P1-4: ``beta -> src/alpha`` made ``src/alpha/a.py`` both
``alpha.a`` and ``beta.a``. The model counts a link by where it leads, so the
two names collapsed onto one location and were never compared; Towel wrote
``from beta.a import ...`` into a module that had loaded only ``alpha.a``, and
``a.py``'s import-time code ran a second time. Each way one file gets two
names is checked here: a directory link, a file link, a link inside a
package, a hard link, and two search-path entries. Each refusal's remedy is
checked to clear it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import Callable, Mapping, Optional

import pytest

import towel
from towel.cli import LINKED_FILE_REMEDY, STRAY_COPY_REMEDY, _import_problem_remedies
from towel.import_model import (
    Doubt,
    FileUnderTwoNames,
    OutsideProvider,
    build_import_model,
    installed_outside,
)

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symbolic links")

_BLOCK = """
def {name}(items):
    total = 0
    for item in items:
        total += len(str(item))
    label = "n=%d" % total
    return label.upper()
"""


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def _standard_library_only(name: str, root: Path) -> Optional[OutsideProvider]:
    if name in sys.stdlib_module_names or name in sys.builtin_module_names:
        return installed_outside(name, root)
    return None


_ALPHA = {
    "pyproject.toml": '[project]\nname = "alpha"\nversion = "0"\n',
    "src/alpha/__init__.py": "",
    "src/alpha/a.py": "print('a loads as', __name__)\n" + _BLOCK.format(name="fa"),
    "src/alpha/b.py": "X = 1\n",
    "src/alpha/impl.py": "",
    "src/alpha/sub/__init__.py": "",
    "src/alpha/sub/m.py": "",
}


def _link(root: Path, link: str, target: str) -> None:
    path = root / link
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(os.path.relpath(root / target, path.parent))


def _links(root: Path, links: Mapping[str, str]) -> None:
    for link, target in links.items():
        _link(root, link, target)


def _link_imported_relatively(root: Path) -> None:
    _write(root, {"src/alpha/uses.py": "from .legacy import m\n"})
    _link(root, "src/alpha/legacy", "src/alpha/sub")


def _hard_link(root: Path, link: str, target: str) -> None:
    os.link(root / target, root / link)


Alias = Callable[[Path], None]

_ALIASES: Mapping[str, tuple[Alias, str, str]] = {
    # How the second name arises, what imports both, and the doubt it reports.
    "directory-link": (
        lambda root: _link(root, "beta", "src/alpha"),
        "import alpha.a\nimport beta.b\n",
        "src/alpha is reachable both as alpha and as beta, through the link beta",
    ),
    "two-directory-links": (
        lambda root: _links(root, {"beta": "src/alpha", "gamma": "src/alpha"}),
        "import beta.b\nimport gamma.b\n",
        "src/alpha is reachable both as beta and as gamma, through the link beta",
    ),
    "file-link": (
        lambda root: _link(root, "tool.py", "src/alpha/a.py"),
        "import alpha.a\nimport tool\n",
        "src/alpha/a.py is reachable both as alpha.a and as tool, through the link tool.py",
    ),
    "subpackage-link": (
        lambda root: _link(root, "beta", "src/alpha/sub"),
        "import alpha.a\nimport beta.m\n",
        "src/alpha/sub is reachable both as alpha.sub and as beta, through the link beta",
    ),
    "file-link-in-a-package": (
        lambda root: _link(root, "src/alpha/compat.py", "src/alpha/impl.py"),
        "import alpha.a\nimport alpha.compat\n",
        "src/alpha/impl.py is reachable both as alpha.compat and as alpha.impl, through the link"
        " src/alpha/compat.py",
    ),
    "directory-link-in-a-package": (
        lambda root: _link(root, "src/alpha/legacy", "src/alpha/sub"),
        "import alpha.a\nfrom alpha.legacy import m\n",
        "src/alpha/sub is reachable both as alpha.legacy and as alpha.sub, through the link"
        " src/alpha/legacy",
    ),
    "relative-import-through-a-link": (
        lambda root: _link_imported_relatively(root),
        "import alpha.a\n",
        "src/alpha/sub is reachable both as alpha.legacy and as alpha.sub, through the link"
        " src/alpha/legacy",
    ),
    "hard-link": (
        lambda root: _hard_link(root, "tool.py", "src/alpha/a.py"),
        "import alpha.a\nimport tool\n",
        "src/alpha/a.py is reachable both as alpha.a and as tool, through the link tool.py",
    ),
}


@pytest.mark.parametrize("alias", sorted(_ALIASES))
def test_a_file_a_link_gives_a_second_name_is_in_doubt(tmp_path: Path, alias: str) -> None:
    make, imports, described = _ALIASES[alias]
    root = _write(tmp_path, {**_ALPHA, "tests/test_a.py": imports})
    make(root)
    model = build_import_model(root, installed=_standard_library_only)
    (problem,) = model.problems
    assert isinstance(problem, FileUnderTwoNames), problem
    assert problem.describe(model.root) == described
    assert problem.doubts == {Doubt.LINK}
    assert _import_problem_remedies([problem]) == LINKED_FILE_REMEDY
    assert problem.names_in_doubt and not any(
        model.names[name].trusted for name in problem.names_in_doubt
    )
    assert model.spelling(model.root / "tests/test_a.py", model.root / "src/alpha/a.py") is None
    # The remedy: a copy in place of the link is a second file with a name of its own.
    assert problem.link is not None
    _replace_with_a_copy(problem.link)
    assert build_import_model(root, installed=_standard_library_only).problems == ()


def _replace_with_a_copy(link: Path) -> None:
    """``link`` replaced by a copy of the file or directory it names, as the remedy says."""
    target = link.resolve()
    if target.is_dir():
        link.unlink()
        shutil.copytree(target, link)
    else:  # A file link, symbolic or hard: its own directory entry, holding the same bytes.
        content = link.read_bytes()
        link.unlink()
        link.write_bytes(content)


@pytest.mark.parametrize("linked", [False, True], ids=["plain", "beside-a-link-of-its-name"])
def test_two_search_path_entries_are_a_stray_package_not_a_link(
    tmp_path: Path, linked: bool
) -> None:
    """A link spelled as the directory it names gives it no second name, so is not the cause."""
    root = _write(tmp_path, {**_ALPHA, "tests/test_a.py": "import alpha.a\nimport src.alpha.b\n"})
    if linked:
        _link(root, "lib/alpha", "src/alpha")
    (problem,) = build_import_model(root, installed=_standard_library_only).problems
    assert isinstance(problem, FileUnderTwoNames) and problem.doubts == {Doubt.TREE}
    assert problem.link is None
    assert _import_problem_remedies([problem]) == STRAY_COPY_REMEDY


@pytest.mark.parametrize(
    "make, imports",
    [
        # The link is the package's only name: a src layout kept elsewhere.
        (lambda root: _link(root, "lib/alpha", "src/alpha"), "import alpha.a\n"),
        # Nothing imports through a link inside the package.
        (lambda root: _link(root, "src/alpha/compat.py", "src/alpha/impl.py"), "import alpha.a\n"),
        # Only a type-only import goes through it, and that never runs.
        (
            lambda root: _link(root, "src/alpha/compat.py", "src/alpha/impl.py"),
            "import alpha.a\nfrom typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    import alpha.compat\n",
        ),
        # A hard link no name reaches twice.
        (lambda root: _hard_link(root, "notes/a_copy.py", "src/alpha/a.py"), "import alpha.a\n"),
    ],
    ids=["only-name", "not-imported-through", "type-only-through", "hard-link-unnamed"],
)
def test_a_link_that_gives_no_second_name_is_no_problem(
    tmp_path: Path, make: Alias, imports: str
) -> None:
    root = _write(tmp_path, {**_ALPHA, "notes/README.txt": "", "tests/test_a.py": imports})
    make(root)
    model = build_import_model(root, installed=_standard_library_only)
    assert model.problems == ()
    assert model.names["alpha"].trusted


# -- The audited layout, and the remedy ------------------------------------------------

_AUDITED = {
    "pyproject.toml": '[project]\nname = "alpha"\nversion = "0"\n',
    "src/alpha/__init__.py": "",
    "src/alpha/a.py": "print('module a loads as', __name__)\n" + _BLOCK.format(name="fa"),
    "src/alpha/b.py": "X = 1\n",
    "app/__init__.py": "",
    "app/main.py": "import alpha.a\nimport beta.b\n" + _BLOCK.format(name="fm"),
}


def _towel(root: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            ".",
            ".",
            "--progress",
            "none",
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--cross-module",
            *flags,
        ],
        capture_output=True,
        text=True,
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )


def _program(root: Path) -> str:
    prelude = f"import sys\nsys.path[:0] = [{str(root)!r}, {str(root / 'src')!r}]\n"
    code = prelude + "import app.main as m\nprint(m.fm([1, 22]))\n"
    ran = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120
    )
    return ran.stdout + ran.stderr


def test_the_audited_alias_refuses_and_a_copy_in_place_of_the_link_clears_it(
    tmp_path: Path,
) -> None:
    """``a.py`` prints once as ``alpha.a`` before and after; a copy is a second file, not a name."""
    root = _write(tmp_path / "project", _AUDITED)
    _link(root, "beta", "src/alpha")
    expected = _program(root)
    assert expected.count("module a loads as") == 1, expected
    refused = _towel(root)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "src/alpha is reachable both as alpha and as beta, through the link beta" in (
        refused.stderr
    )
    assert LINKED_FILE_REMEDY in refused.stderr
    assert "import __extracted_func" not in (root / "app/main.py").read_text()
    # The remedy: the link replaced by a copy of what it names.
    (root / "beta").unlink()
    shutil.copytree(root / "src" / "alpha", root / "beta")
    expected = _program(root)
    ran = _towel(root)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "reachable both as" not in ran.stderr
    assert _program(root) == expected


def test_a_guarded_import_that_succeeds_still_loads_a_second_name(tmp_path: Path) -> None:
    """It attests nothing, so places no name; but where it succeeds, the file loads again."""
    guarded = "import alpha.a\ntry:\n    import src.alpha.b\nexcept ImportError:\n    pass\n"
    root = _write(tmp_path, {**_ALPHA, "tests/test_a.py": guarded})
    (problem,) = build_import_model(root, installed=_standard_library_only).problems
    assert isinstance(problem, FileUnderTwoNames)
    assert problem.names == ("src.alpha", "alpha")
