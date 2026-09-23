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

"""What a complete project check walks, and what it must not.

The check exists to see the modules around the changed ones. Walking the
repository root in their place reaches everything else a repository holds, and
one file mypy cannot build fails the request and refuses the project. Seventeen
of the first fifty-two projects of the release corpus were refused that way:
for test data written to be invalid, for a demo script sharing a module name
with another, for a stub beside the module it describes.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import textwrap
from typing import Mapping

import pytest

from towel import consumers
from towel._mypy_worker import _walked_for
from towel.consumers import consumers_of
from towel import type_inference
from towel.type_inference import CheckFailure, CheckSuccess, MypyInferrer

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _check(root: Path, *analyzed: str) -> CheckSuccess | CheckFailure:
    oracle = MypyInferrer()
    try:
        return oracle.check_project(
            {str(root / name): (root / name).read_text(encoding="utf-8") for name in analyzed}
        )
    finally:
        oracle.close()


def test_a_file_in_a_package_is_walked_as_its_top_package(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/inner/__init__.py": "", "pkg/inner/m.py": ""})
    assert _walked_for(tmp_path / "pkg" / "inner" / "m.py") == tmp_path / "pkg"
    assert _walked_for(tmp_path / "pkg" / "__init__.py") == tmp_path / "pkg"


def test_a_module_belonging_to_no_package_is_walked_alone(tmp_path: Path) -> None:
    _write(tmp_path, {"lonely.py": ""})
    assert _walked_for(tmp_path / "lonely.py") == tmp_path / "lonely.py"


def test_a_stub_marks_a_package_too(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/__init__.pyi": "", "pkg/m.py": ""})
    assert _walked_for(tmp_path / "pkg" / "m.py") == tmp_path / "pkg"


@requires_mypy
def test_invalid_test_data_outside_the_package_does_not_refuse_the_project(tmp_path: Path) -> None:
    """pycodestyle, Pygments, Black and Django all ship a file like this."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.py": "",
            "pkg/m.py": "def tag(name: str) -> str:\n    return name.upper()\n",
            "tests/data/broken.py": "print 'this was never Python 3'\n",
            "tests/data/tabs.py": "def f():\n\tif True:\n        return 1\n",
        },
    )
    result = _check(tmp_path, "pkg/m.py")
    assert isinstance(result, CheckSuccess), result
    assert result.errors == ()


@requires_mypy
def test_two_scripts_sharing_a_module_name_do_not_refuse_the_project(tmp_path: Path) -> None:
    """pluggy has ``setup.py`` twice under its docs; Tornado has two demos."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.py": "",
            "pkg/m.py": "def tag(name: str) -> str:\n    return name.upper()\n",
            "demos/one/demo.py": "",
            "demos/two/demo.py": "",
        },
    )
    assert isinstance(_check(tmp_path, "pkg/m.py"), CheckSuccess)


@requires_mypy
def test_a_package_shipping_its_own_stub_is_checked_not_refused(tmp_path: Path) -> None:
    """attrs and more-itertools both ship ``__init__.pyi`` beside ``__init__.py``."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.pyi": "def tag(name: str) -> str: ...\n",
            "pkg/__init__.py": "def tag(name: str) -> str:\n    return name.upper()\n",
        },
    )
    result = _check(tmp_path, "pkg/__init__.py")
    assert isinstance(result, CheckSuccess), result


@requires_mypy
def test_the_file_being_changed_is_the_one_checked_even_under_a_stub(tmp_path: Path) -> None:
    """Dropping the implementation for its stub would check nothing Towel changes."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.pyi": "def tag(name: str) -> str: ...\n",
            "pkg/__init__.py": "def tag(name: str) -> str:\n    return name.bogus_attribute\n",
        },
    )
    result = _check(tmp_path, "pkg/__init__.py")
    assert isinstance(result, CheckSuccess), result
    assert result.errors, "the stub answered for the module and nothing was checked"
    assert {error.path for error in result.errors} == {str(tmp_path / "pkg" / "__init__.py")}


@requires_mypy
def test_a_consumer_inside_the_package_is_still_checked(tmp_path: Path) -> None:
    """The walk must not shrink to the analyzed files: that is what it is for."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.py": "",
            "pkg/m.py": "def tag(name: str) -> str:\n    return name.upper()\n",
            "pkg/consumer.py": "from .m import tag\n\nBAD: int = tag('x')\n",
        },
    )
    result = _check(tmp_path, "pkg/m.py")
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(tmp_path / "pkg" / "consumer.py")]


@requires_mypy
def test_a_config_naming_its_own_files_is_obeyed(tmp_path: Path) -> None:
    """Where the project has said what it checks, that is what is checked."""
    _write(
        tmp_path,
        {
            "pyproject.toml": '[tool.mypy]\nstrict = true\nfiles = ["pkg", "extra"]\n',
            "pkg/__init__.py": "",
            "pkg/m.py": "def tag(name: str) -> str:\n    return name.upper()\n",
            "extra/__init__.py": "",
            "extra/wrong.py": "BAD: int = 'not an int'\n",
        },
    )
    result = _check(tmp_path, "pkg/m.py")
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(tmp_path / "extra" / "wrong.py")]


@requires_mypy
def test_an_unchanged_consumer_outside_the_package_is_checked(tmp_path: Path) -> None:
    """The consumer can only be reached by looking for what imports the change.

    A subclass in another package, unchanged and never imported by the package
    it extends, is broken by a helper whose name it already uses. Following
    imports out of the package never reaches it, so a check scoped to the
    package alone called the project clean while the program's answer changed.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "lib/__init__.py": "",
            "lib/base.py": "class Base:\n    def first(self, value: int) -> int:\n        return value\n",
            "consumer.py": (
                "from lib.base import Base\n\n\n"
                "class Child(Base):\n"
                "    def _extracted_func_0(self, value: int) -> str:\n"
                "        return 'surprise'\n"
            ),
        },
    )
    collides = (
        "class Base:\n"
        "    def first(self, value: int) -> int:\n"
        "        return self._extracted_func_0(value)\n\n"
        "    def _extracted_func_0(self, value: int) -> int:\n"
        "        return value\n"
    )
    oracle = MypyInferrer()
    try:
        result = oracle.check_project({str(tmp_path / "lib" / "base.py"): collides})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(tmp_path / "consumer.py")], result


@requires_mypy
def test_a_consumer_of_a_consumer_is_reached_too(tmp_path: Path) -> None:
    """A test helper imports the package and the tests import the helper."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "lib/__init__.py": "",
            "lib/base.py": "VALUE: int = 1\n",
            "helper.py": "from lib.base import VALUE\n\nSHARED: int = VALUE\n",
            "uses_helper.py": "from helper import SHARED\n\nWRONG: str = SHARED\n",
        },
    )
    oracle = MypyInferrer()
    try:
        result = oracle.check_project({str(tmp_path / "lib" / "base.py"): "VALUE: int = 1\n"})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(tmp_path / "uses_helper.py")], result


@requires_mypy
def test_an_unparseable_file_is_not_a_consumer_and_does_not_refuse_the_project(
    tmp_path: Path,
) -> None:
    """It imports nothing, so it can be neither reached by a change nor built."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "lib/__init__.py": "",
            "lib/base.py": "VALUE: int = 1\n",
            "tests/data/broken.py": "import lib\nprint 'never Python 3'\n",
        },
    )
    oracle = MypyInferrer()
    try:
        result = oracle.check_project({str(tmp_path / "lib" / "base.py"): "VALUE: int = 1\n"})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert result.errors == ()


@requires_mypy
def test_two_unrelated_files_claiming_one_module_are_refused_not_collapsed(
    tmp_path: Path,
) -> None:
    """Collapsing them would drop the second file's errors and report clean.

    A stub and the implementation beside it are one module's two faces and do
    collapse. Two directories that are not packages, each holding ``module.py``,
    are two modules with one inferred name, and mypy refuses that build. The
    refusal is the right answer: it is loud and it names both files.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "a/module.py": "x: int = 1\n",
            "b/module.py": "x: int = 'wrong'\n",
        },
    )
    result = _check(tmp_path, "a/module.py", "b/module.py")
    assert isinstance(result, CheckFailure), result
    assert "Duplicate module" in result.reason


@requires_mypy
def test_the_consumer_scan_is_paid_for_once_however_many_checks_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prospective check names a few modules; it must not rescan the project.

    Verification checks the project once per candidate signature, hundreds of
    times in a run, and each of those requests names only the modules it is
    about. Scanning afresh for each would parse the whole tree every time:
    0.31 s on Sphinx, where a capped run makes 54 such checks. What a sparse
    check pays is a walk and a stat per file.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.py": "",
            "pkg/one.py": "VALUE: int = 1\n",
            "pkg/two.py": "OTHER: int = 2\n",
            "consumer.py": "from pkg.one import VALUE\n\nUSED: int = VALUE\n",
        },
    )
    parses = 0
    original = ast.parse

    def counting(*arguments: object, **keywords: object) -> object:
        nonlocal parses
        parses += 1
        return original(*arguments, **keywords)  # type: ignore[call-overload]

    monkeypatch.setattr(ast, "parse", counting)
    one, two = str(tmp_path / "pkg" / "one.py"), str(tmp_path / "pkg" / "two.py")
    oracle = MypyInferrer()
    try:
        oracle.check_project({one: "VALUE: int = 1\n", two: "OTHER: int = 2\n"})
        assert parses == 4, "the first complete check pays for the scan"
        oracle.check_project({one: "VALUE: int = 3\n"})
        oracle.check_project({two: "OTHER: int = 4\n"})
        oracle.check_project({one: "VALUE: int = 5\n"})
    finally:
        oracle.close()
    assert parses == 4, f"a sparse check parsed the project again ({parses} parses)"


@requires_mypy
def test_a_consumer_reached_through_a_package_reexport_is_checked(tmp_path: Path) -> None:
    """``from .sub import SHARED`` in ``app/__init__.py`` names ``app.sub``.

    The relative import was resolved against the parent of the ``__init__``
    module, reading ``sub``, so a file importing only the re-exporting package
    was never reached, and the complete check never looked at it.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "lib/__init__.py": "",
            "lib/base.py": "VALUE: int = 1\n",
            "app/__init__.py": "from .sub import SHARED as SHARED\n",
            "app/sub.py": "from lib.base import VALUE\n\nSHARED: int = VALUE\n",
            "uses_app.py": "from app import SHARED\n\nWRONG: str = SHARED\n",
        },
    )
    oracle = MypyInferrer()
    try:
        result = oracle.check_project({str(tmp_path / "lib" / "base.py"): "VALUE: int = 1\n"})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(tmp_path / "uses_app.py")], result


def test_a_relative_import_in_an_init_resolves_against_the_package_itself(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "lib/__init__.py": "",
            "lib/core.py": "X = 1\n",
            "app/__init__.py": "from .sub import y as y\n",
            "app/sub.py": "from lib.core import X\n\ny = X\n",
            "tests/test_x.py": "from app import y\n",
        },
    )
    found = consumers_of(
        tmp_path,
        {"lib", "lib.core"},
        module_name=lambda path: type_inference._module_name_and_root(path)[0],
        exclude=[tmp_path / "lib"],
    )
    assert found == sorted(
        str(tmp_path / name) for name in ("app/__init__.py", "app/sub.py", "tests/test_x.py")
    )


def test_a_tree_beyond_the_scan_limit_is_refused_not_scanned_in_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial consumer list would make the complete check silently incomplete."""
    monkeypatch.setattr(consumers, "MAXIMUM_FILES", 2)
    _write(tmp_path, {"lib/__init__.py": "", "a.py": "import lib\n", "b.py": "", "c.py": ""})
    with pytest.raises(consumers.ScanLimitExceeded):
        consumers_of(tmp_path, {"lib"}, module_name=lambda path: path.stem)


TWIN_FUNCTIONS = "def tag(name: str) -> str:\n    return name.upper()\n"
UNCHECKED_TEST = "from pkg.m import tag\n\n\ndef test_tag():\n    x: int = 'wrong'\n"


@requires_mypy
@pytest.mark.parametrize("config", ['exclude = ["^tests/"]', 'files = ["pkg"]'])
def test_a_file_the_project_does_not_check_is_not_held_against_it(
    tmp_path: Path, config: str
) -> None:
    """The project's own mypy is clean; the baseline must be too.

    ``towel dry . .`` analyses ``tests/`` as well, and every analysed file was
    checked whatever the configuration said, so a project whose mypy run is
    clean was refused for errors in files that run never looks at.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": f"[tool.mypy]\nstrict = true\n{config}\n",
            "pkg/__init__.py": "",
            "pkg/m.py": TWIN_FUNCTIONS,
            "tests/test_m.py": UNCHECKED_TEST,
        },
    )
    result = _check(tmp_path, "pkg/__init__.py", "pkg/m.py", "tests/test_m.py")
    assert isinstance(result, CheckSuccess), result
    assert result.errors == ()


@requires_mypy
def test_an_excluded_module_the_checked_code_imports_is_still_checked(tmp_path: Path) -> None:
    """mypy follows an import into an excluded file and reports it, and so must this."""
    _write(
        tmp_path,
        {
            "pyproject.toml": '[tool.mypy]\nstrict = true\nexclude = ["^pkg/_vendor/"]\n',
            "pkg/__init__.py": "",
            "pkg/_vendor/__init__.py": "",
            "pkg/_vendor/v.py": "VALUE: int = 1\n",
            "pkg/m.py": "from pkg._vendor.v import VALUE\n\nUSED: int = VALUE\n",
        },
    )
    oracle = MypyInferrer()
    try:
        result = oracle.check_project(
            {
                str(tmp_path / "pkg" / "m.py"): (tmp_path / "pkg" / "m.py").read_text(),
                str(tmp_path / "pkg" / "_vendor" / "v.py"): "VALUE: int = 'broken'\n",
            }
        )
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(tmp_path / "pkg" / "_vendor" / "v.py")]
