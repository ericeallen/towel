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

"""With --cross-module, a run refuses when the program's imports leave its own package's names in doubt.

A helper shared across modules is imported by the name the program's own
imports give its host. Before anything is written, a ``--cross-module`` run
names every problem those imports have, with the remedy. One that leaves a
name of the package being refactored in doubt refuses the run: anyio's stale
``build/lib/anyio`` beside ``anyio`` makes every name of the package
ambiguous. One that involves only other modules is reported and the run goes
on; the model already declines what it involves. An import of a module the
tree lacks, as sphinx's test data, a stale prompt-toolkit example and a
package's import of its generated ``_version.py`` make, leaves no name in
doubt wherever it lies: the run goes on and leaves the file making it exactly
as it was. Without ``--cross-module`` no import that runs is written, so no
problem can matter, and none is reported.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import Callable, Mapping, Sequence

import pytest

import towel
from towel.cli import _problems_involving
from towel.import_model import build_import_model

_BLOCK = """
def {name}(values):
    print({tag!r})
    total = 0
    for value in values:
        if value > 1:
            total += value * 2
        else:
            total -= value
    total = total + 1
    return total
"""

_WITHIN = """
def {name}(words):
    print({tag!r})
    seen = []
    for word in words:
        cleaned = word.strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return ", ".join(seen)
"""

_SETUPTOOLS = (
    '[build-system]\nrequires = ["setuptools>=61"]\n'
    'build-backend = "setuptools.build_meta"\n'
    '[project]\nname = "{name}"\nversion = "0"\n'
)


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def _project(root: Path, extra: Mapping[str, str] = {}) -> Path:
    """``zzalpha`` under ``src``: a block shared by two modules, another within ``b``."""
    return _write(
        root,
        {
            "pyproject.toml": _SETUPTOOLS.format(name="zzalpha")
            + '[tool.setuptools.packages.find]\nwhere = ["src"]\n',
            "src/zzalpha/__init__.py": "",
            "src/zzalpha/a.py": _BLOCK.format(name="fa", tag="a"),
            "src/zzalpha/b.py": _BLOCK.format(name="fb", tag="b")
            + _WITHIN.format(name="gb", tag="gb")
            + _WITHIN.format(name="hb", tag="hb"),
            "tests/test_a.py": "import zzalpha.a\n",
            **extra,
        },
    )


def _sphinx_shaped(root: Path) -> Path:
    """sphinx's shape: a package importing itself absolutely, and test data importing what it lacks.

    sphinx's autodoc tests mock ``sphinx.missing_module4``, which
    ``tests/roots/test-ext-autodoc/target/need_mocks.py`` imports.
    """
    return _write(
        root,
        {
            "pyproject.toml": _SETUPTOOLS.format(name="zzsphx")
            + '[tool.setuptools]\npackages = ["zzsphx"]\n',
            "zzsphx/__init__.py": "",
            "zzsphx/util.py": "SCALE = 2\n",
            "zzsphx/a.py": "from zzsphx.util import SCALE\n" + _BLOCK.format(name="fa", tag="a"),
            "zzsphx/b.py": "from zzsphx.util import SCALE\n" + _BLOCK.format(name="fb", tag="b"),
            "tests/test_a.py": "import zzsphx.a\n",
            "tests/roots/test-ext-autodoc/target/__init__.py": "",
            "tests/roots/test-ext-autodoc/target/need_mocks.py": (
                "import missing_module\n"
                "import zzsphx.missing_module4\n"
                "from zzsphx.missing_module4 import missing_name2\n"
            )
            + _BLOCK.format(name="fm", tag="m")
            + _WITHIN.format(name="gm", tag="gm")
            + _WITHIN.format(name="hm", tag="hm"),
        },
    )


def _prompt_toolkit_shaped(root: Path) -> Path:
    """prompt-toolkit's shape: a src-layout package, and examples importing what does not exist.

    ``examples/gevent-get-input.py`` imports
    ``prompt_toolkit.eventloop.defaults``, which the package no longer has.
    """
    return _write(
        root,
        {
            "pyproject.toml": _SETUPTOOLS.format(name="zzprompt")
            + '[tool.setuptools.packages.find]\nwhere = ["src"]\n',
            "src/zzprompt/__init__.py": "",
            "src/zzprompt/eventloop/__init__.py": "from .inputhook import fa\n",
            "src/zzprompt/eventloop/inputhook.py": _BLOCK.format(name="fa", tag="a"),
            "src/zzprompt/shortcuts.py": "from .eventloop import inputhook\n"
            + _BLOCK.format(name="fb", tag="b"),
            "tests/test_shortcuts.py": "import zzprompt.shortcuts\n",
            "examples/gevent-get-input.py": (
                "from gevent.monkey import patch_all\n\n"
                "from zzprompt.eventloop.defaults import create_event_loop\n"
                "from zzprompt.shortcuts import PromptSession\n"
            ),
            "examples/no-such-module.py": "import zz_towel_no_such_module\n",
        },
    )


_STALE_COPY = {"build/lib/zzalpha/__init__.py": "", "build/lib/zzalpha/a.py": "VALUE = 1\n"}
_BROKEN_TEST_DATA = {
    "tests/data/broken/__init__.py": "",
    "tests/data/broken/mod.py": "from .missing import thing\n",
}


_INSIDE_REMEDY = (
    "A module generated at build time, such as a _version.py, appears once the project is"
    " installed (pip install -e .); fix any other."
)
_VERSION = {"src/zzalpha/_version.py": 'version = "1.0"\n'}
_IMPORTS_VERSION = "from zzalpha._version import version\n"


def _towel(
    root: Path, command: str, *flags: str, target: str = "src/zzalpha"
) -> subprocess.CompletedProcess[str]:
    arguments = [command, target, target] if command == "dry" else [command, target]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            *arguments,
            "--progress",
            "none",
            *(("--no-interactive", "--no-types", "--no-format") if command == "dry" else ()),
            *flags,
        ],
        capture_output=True,
        text=True,
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )


def _sources(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }


def _behaviour(
    root: Path, sys_path: Sequence[str], code: str, generated: Mapping[str, str] = {}
) -> str:
    """What ``code`` prints with only ``sys_path`` under ``root`` added: the program as its users import it.

    ``generated`` are the modules a build writes, which the tree lacks: the
    program runs from a copy that holds them, as an installed project does.
    """
    if generated:
        installed = root.parent / f"{root.name}-installed"
        shutil.copytree(root, installed)
        root = _write(installed, generated)
    prelude = f"import sys\nsys.path[:0] = {[str(root / entry) for entry in sys_path]!r}\n"
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", prelude + code],
            capture_output=True,
            text=True,
            cwd=root.parent,
            env={"PATH": os.environ.get("PATH", "")},
            timeout=120,
        )
    finally:
        if generated:
            shutil.rmtree(root)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _refactored_alike(
    root: Path,
    sys_path: Sequence[str],
    program: str,
    refactor: Callable[[], subprocess.CompletedProcess[str]],
    generated: Mapping[str, str] = {},
) -> subprocess.CompletedProcess[str]:
    """Run ``refactor`` on ``root``, and check ``program`` prints what it printed before."""
    expected = _behaviour(root, sys_path, program, generated)
    ran = refactor()
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Refusing" not in ran.stderr, ran.stderr
    assert _behaviour(root, sys_path, program, generated) == expected
    return ran


def test_a_problem_of_the_package_being_refactored_refuses_the_run(tmp_path: Path) -> None:
    root = _project(tmp_path / "project", _STALE_COPY)
    before = _sources(root)
    for command in ("dry", "preview"):
        refused = _towel(root, command, "--cross-module")
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert "Refusing to share helpers across the modules of" in refused.stderr, refused.stderr
        assert "zzalpha could be any of: build/lib/zzalpha; src/zzalpha" in refused.stderr
        assert "--exclude <directory name> (for example --exclude build)" in refused.stderr
        assert "APPLYING" not in refused.stdout and "Analyzing" not in refused.stdout
        assert _sources(root) == before
    # Set the stray copy aside and the run shares the helper.
    ran = _towel(root, "dry", "--cross-module", "--exclude", "build")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "could be any of" not in ran.stderr
    assert "from .a import __extracted_func" in (root / "src/zzalpha/b.py").read_text()


def test_a_problem_only_in_the_test_data_is_reported_and_the_run_goes_on(tmp_path: Path) -> None:
    root = _project(tmp_path / "project", _BROKEN_TEST_DATA)
    ran = _towel(root, "dry", "--cross-module")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "tests/data/broken/mod.py:1: from .missing import thing names .missing" in ran.stderr
    assert "--exclude <directory name>" in ran.stderr
    assert "from .a import __extracted_func" in (root / "src/zzalpha/b.py").read_text()


def test_test_data_importing_what_the_package_lacks_leaves_its_helpers_shared(
    tmp_path: Path,
) -> None:
    """sphinx's mocked ``sphinx.missing_module4`` once refused every run on sphinx and flagged sphinx."""
    root = _sphinx_shaped(tmp_path / "project")
    program = (
        "import zzsphx.a, zzsphx.b\nprint(zzsphx.a.fa([0, 1, 2, 3]), zzsphx.b.fb([3, -1, 5]))\n"
    )
    expected = _behaviour(root, ["."], program)
    ran = _towel(root, "dry", "--cross-module", target="zzsphx")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Refusing" not in ran.stderr
    assert (
        "tests/roots/test-ext-autodoc/target/need_mocks.py:2: import zzsphx.missing_module4"
        " needs zzsphx.missing_module4, which zzsphx does not hold"
    ) in ran.stderr
    assert "--exclude <directory name>" in ran.stderr
    # Each module spells its own package absolutely, and the shared helper is imported so.
    a, b = ((root / "zzsphx" / name).read_text() for name in ("a.py", "b.py"))
    assert "def __extracted_func" in a, a
    assert "from zzsphx.a import __extracted_func" in b, b
    assert _behaviour(root, ["."], program) == expected
    written = "".join(text for path, text in _sources(root).items() if "need_mocks" not in path)
    assert "missing_module4" not in written


def test_a_run_from_the_root_leaves_test_data_importing_what_the_package_lacks_unchanged(
    tmp_path: Path,
) -> None:
    """From the project root the test data is part of what is refactored, and is left as it was."""
    root = _sphinx_shaped(tmp_path / "project")
    fixture = root / "tests/roots/test-ext-autodoc/target/need_mocks.py"
    before = fixture.read_bytes()
    program = (
        "import zzsphx.a, zzsphx.b\nprint(zzsphx.a.fa([0, 1, 2, 3]), zzsphx.b.fb([3, -1, 5]))\n"
    )
    ran = _refactored_alike(
        root, ["."], program, lambda: _towel(root, "dry", "--cross-module", target=".")
    )
    assert "need_mocks.py:2: import zzsphx.missing_module4" in ran.stderr
    assert (
        "Left unchanged: tests/roots/test-ext-autodoc/target/need_mocks.py. Fix the import, or"
        " leave its directory out with --exclude <directory name>."
    ) in ran.stderr
    # Its block is duplicated in the package and within itself, and neither moved.
    assert fixture.read_bytes() == before
    assert "from zzsphx.a import __extracted_func" in (root / "zzsphx/b.py").read_text()


def test_an_example_importing_what_the_package_lacks_leaves_its_helpers_shared(
    tmp_path: Path,
) -> None:
    """prompt-toolkit's stale example once refused every run on prompt_toolkit and flagged it."""
    root = _prompt_toolkit_shaped(tmp_path / "project")
    program = (
        "import zzprompt.shortcuts\nfrom zzprompt.eventloop import inputhook\n"
        "print(zzprompt.shortcuts.fb([0, 1, 2, 3]), inputhook.fa([3, -1, 5]))\n"
    )
    expected = _behaviour(root, ["src"], program)
    ran = _towel(root, "dry", "--cross-module", target="src/zzprompt")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Refusing" not in ran.stderr
    assert (
        "examples/gevent-get-input.py:3: from zzprompt.eventloop.defaults import"
        " create_event_loop needs zzprompt.eventloop.defaults, which src/zzprompt does not hold"
    ) in ran.stderr
    # A missing top-level module is some other distribution's, and no problem at all.
    assert "zz_towel_no_such_module" not in ran.stderr
    shortcuts = (root / "src/zzprompt/shortcuts.py").read_text()
    hook = (root / "src/zzprompt/eventloop/inputhook.py").read_text()
    assert "def __extracted_func" in hook, hook
    assert "from .eventloop.inputhook import __extracted_func" in shortcuts, shortcuts
    assert _behaviour(root, ["src"], program) == expected


def test_an_unresolved_import_inside_the_package_leaves_its_file_unchanged(
    tmp_path: Path,
) -> None:
    broken = "from zzalpha.gone import thing\n" + _WITHIN.format(name="gc", tag="gc")
    root = _project(
        tmp_path / "project",
        {"src/zzalpha/c.py": broken + _WITHIN.format(name="hc", tag="hc")},
    )
    before = (root / "src/zzalpha/c.py").read_bytes()
    program = (
        "import zzalpha.a, zzalpha.b\nprint(zzalpha.a.fa([0, 1, 2, 3]), zzalpha.b.fb([3, -1, 5]))\n"
    )
    ran = _refactored_alike(root, ["src"], program, lambda: _towel(root, "dry", "--cross-module"))
    assert "src/zzalpha/c.py:1: from zzalpha.gone import thing needs zzalpha.gone" in ran.stderr
    assert f"Left unchanged: src/zzalpha/c.py. {_INSIDE_REMEDY}" in ran.stderr
    assert (root / "src/zzalpha/c.py").read_bytes() == before
    assert "from .a import __extracted_func" in (root / "src/zzalpha/b.py").read_text()


def test_a_package_importing_its_generated_version_is_refactored_around_its_initializer(
    tmp_path: Path,
) -> None:
    """A fresh clone lacks the ``_version.py`` its build writes; seven corpus packages import one."""
    initializer = (
        _IMPORTS_VERSION
        + _BLOCK.format(name="fi", tag="i")
        + _WITHIN.format(name="gi", tag="gi")
        + _WITHIN.format(name="hi", tag="hi")
    )
    root = _project(tmp_path / "project", {"src/zzalpha/__init__.py": initializer})
    before = (root / "src/zzalpha/__init__.py").read_bytes()
    program = (
        "import zzalpha, zzalpha.a, zzalpha.b\n"
        "print(zzalpha.version, zzalpha.fi([2]), zzalpha.a.fa([0, 1, 2, 3]), zzalpha.b.fb([3, -1]))\n"
    )
    ran = _refactored_alike(
        root, ["src"], program, lambda: _towel(root, "dry", "--cross-module"), _VERSION
    )
    assert "src/zzalpha/__init__.py:1: from zzalpha._version import version" in ran.stderr
    assert f"Left unchanged: src/zzalpha/__init__.py. {_INSIDE_REMEDY}" in ran.stderr
    assert "--exclude" not in ran.stderr
    assert (root / "src/zzalpha/__init__.py").read_bytes() == before
    # Every module of the package is imported through the initializer, so its
    # import has already run wherever the helper is newly imported.
    assert "from .a import __extracted_func" in (root / "src/zzalpha/b.py").read_text()


def test_a_subpackage_below_an_initializer_importing_what_the_tree_lacks_is_refactored(
    tmp_path: Path,
) -> None:
    """The initializer runs for every module of the subpackage; the run says what that costs."""
    root = _project(
        tmp_path / "project",
        {
            "src/zzalpha/__init__.py": _IMPORTS_VERSION,
            "src/zzalpha/sub/__init__.py": "",
            "src/zzalpha/sub/m.py": _BLOCK.format(name="fm", tag="m"),
            "src/zzalpha/sub/n.py": _BLOCK.format(name="fn", tag="n"),
        },
    )
    program = (
        "import zzalpha.sub.m, zzalpha.sub.n\n"
        "print(zzalpha.sub.m.fm([0, 1, 2, 3]), zzalpha.sub.n.fn([3, -1, 5]))\n"
    )
    ran = _refactored_alike(
        root,
        ["src"],
        program,
        lambda: _towel(root, "dry", "--cross-module", target="src/zzalpha/sub"),
        _VERSION,
    )
    assert f"Left unchanged: src/zzalpha/__init__.py. {_INSIDE_REMEDY}" in ran.stderr
    assert (
        "Modules in src/zzalpha host a helper only for modules imported through it: importing"
        " one from anywhere else would run src/zzalpha/__init__.py, whose import the tree"
        " lacks, where it has not run."
    ) in ran.stderr
    assert "from .m import __extracted_func" in (root / "src/zzalpha/sub/n.py").read_text()


@pytest.mark.parametrize(
    "extra, named",
    [
        (_STALE_COPY, "zzalpha could be any of: build/lib/zzalpha; src/zzalpha"),
        (
            {"tests/fixtures/copy/zzalpha/__init__.py": ""},
            "zzalpha could be any of: src/zzalpha; tests/fixtures/copy/zzalpha",
        ),
        (
            {"tools/report.py": "import src.zzalpha.a\n"},
            "src/zzalpha is reachable both as src.zzalpha and as zzalpha",
        ),
        (
            {"src/zzalpha/c.py": "from ... import elsewhere\n"},
            "src/zzalpha/c.py:1: from ... import elsewhere climbs out of its top-level package",
        ),
    ],
    ids=["stale-build-copy", "ambiguous-name", "file-under-two-names", "escaping-relative-import"],
)
def test_a_problem_leaving_a_name_of_the_target_in_doubt_still_refuses(
    tmp_path: Path, extra: Mapping[str, str], named: str
) -> None:
    root = _project(tmp_path / "project", extra)
    before = _sources(root)
    for target in ("src/zzalpha", "."):
        refused = _towel(root, "dry", "--cross-module", target=target)
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert "Refusing to share helpers across the modules of" in refused.stderr
        assert named in refused.stderr, refused.stderr
        assert _sources(root) == before


def test_without_cross_module_no_problem_is_reported_or_refuses(tmp_path: Path) -> None:
    """Only a same-file helper can be written, and no import that runs depends on the names."""
    root = _project(tmp_path / "project", _STALE_COPY)
    ran = _towel(root, "dry")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "could be any of" not in ran.stderr and "--exclude <directory name>" not in ran.stderr
    b = (root / "src/zzalpha/b.py").read_text()
    assert "def __extracted_func_0(" in b, b
    assert "import" not in (root / "src/zzalpha/a.py").read_text()
    previewed = _towel(root, "preview")
    assert previewed.returncode == 0, previewed.stdout + previewed.stderr
    assert "could be any of" not in previewed.stderr


def test_which_problems_involve_the_target(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "project",
        {
            **_STALE_COPY,
            **_BROKEN_TEST_DATA,
        },
    )
    model = build_import_model(root)
    described = {problem.describe(model.root) for problem in model.problems}
    target = root / "src" / "zzalpha"
    involved = {problem.describe(model.root) for problem in _problems_involving(model, target)}
    assert any("could be any of" in text for text in involved)
    assert not any("tests/data/broken" in text for text in involved)
    assert any("tests/data/broken" in text for text in described - involved)
    # From the project root every name is the target's, and every missing module lies in it.
    assert {problem.describe(model.root) for problem in _problems_involving(model, root)} == {
        text for text in described if "tests/data/broken" not in text
    }
    # An import of a module zzalpha lacks puts no name in doubt, only the test making it.
    gone = _project(tmp_path / "gone", {"tests/test_gone.py": "from zzalpha.gone import thing\n"})
    model = build_import_model(gone)
    assert len(model.problems) == 1
    assert _problems_involving(model, gone / "src" / "zzalpha") == []
    assert _problems_involving(model, gone) == []


def test_a_problem_elsewhere_in_the_package_involves_a_subpackage_when_it_leaves_a_name_in_doubt(
    tmp_path: Path,
) -> None:
    """Refusing only for problems inside the target let a run go on with every helper declined."""
    root = _project(
        tmp_path / "project",
        {
            "src/zzalpha/sub/__init__.py": "",
            "src/zzalpha/sub/m.py": "",
            "src/zzalpha/tools/__init__.py": "",
            "src/zzalpha/tools/x.py": "from ... import elsewhere\n",
        },
    )
    target = root / "src" / "zzalpha" / "sub"
    # Where the climb out of zzalpha holds, zzalpha is not the program's name
    # for any of its modules, so every name in the subpackage is in doubt.
    model = build_import_model(root)
    assert len(model.problems) == 1
    assert _problems_involving(model, target) == list(model.problems)
    assert [problem.names_in_doubt for problem in model.problems] == [{"zzalpha"}]
    # An initializer importing what the tree lacks runs for every module of
    # the subpackage, but leaves no name in doubt.
    _write(
        root,
        {"src/zzalpha/tools/x.py": "", "src/zzalpha/__init__.py": "from zzalpha._v import v\n"},
    )
    model = build_import_model(root)
    assert len(model.problems) == 1
    assert _problems_involving(model, target) == []
    assert [problem.names_in_doubt for problem in model.problems] == [frozenset()]


def test_a_copy_installed_outside_the_project_is_given_a_remedy_that_reaches_it() -> None:
    """``--exclude`` cannot leave out site-packages; another environment can (third audit, P2-4)."""
    from towel.cli import _import_problem_remedy
    from towel.import_model import AmbiguousName

    stale = AmbiguousName("zzalpha", (Path("/p/src/zzalpha"), Path("/p/build/lib/zzalpha")), None)
    installed = AmbiguousName("click", (Path("/p/click"),), "/env/site-packages/click/__init__.py")
    assert "editable install" not in _import_problem_remedy([stale])
    remedy = _import_problem_remedy([stale, installed])
    assert remedy.startswith("Leave out each directory holding a stray copy")
    assert "--exclude reaches only the project's tree" in remedy
    assert "an environment where the package is this tree (an editable install)" in remedy
