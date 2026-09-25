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

"""An import under ``if TYPE_CHECKING:`` never runs, so it closes no import cycle.

Towel writes imports there on exactly that premise, for names only a checker
reads. The cycle guard followed them as edges anyway, so two modules that
name each other's classes for the checker, the usual reason for the idiom,
could share no helper in either direction, and a type-only import Towel had
just written refused every later pair between the same two modules. The guard
now skips a guarded body, in any scope, and still follows its ``else``, a
test that is not the name (``not TYPE_CHECKING``), and a ``TYPE_CHECKING``
that resolves to anything else.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping

import importlib.util

import pytest

import towel
from towel.unification.import_graph import (
    ImportGraphCache,
    TypeCheckingGuards,
    _import_statements,
    _required_imports,
    would_create_import_cycle,
)

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

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

_GUARDED = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .{other} import {other_class}


class {own_class}:
    def link(self, other: "{other_class}") -> None:
        self.other = other
"""


def _package(root: Path, alpha: str, beta: str) -> Path:
    files: Mapping[str, str] = {
        "pyproject.toml": "[project]\nname = 'sample'\nversion = '0'\n",
        "tests/test_pkg.py": "import pkg.alpha\nimport pkg.beta\n",
        "pkg/__init__.py": "",
        "pkg/alpha.py": alpha + _BLOCK.format(name="fa", tag="a"),
        "pkg/beta.py": beta + _BLOCK.format(name="fb", tag="b"),
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def _guarded(own: str, other: str) -> str:
    return _GUARDED.format(other=other, own_class=own.capitalize(), other_class=other.capitalize())


def _closes_a_cycle(root: Path, host: str, borrower: str) -> bool:
    return would_create_import_cycle(
        str(root / "pkg" / f"{host}.py"), {str(root / "pkg" / f"{borrower}.py")}, ImportGraphCache()
    )


def test_a_cycle_only_through_type_checking_imports_is_none(tmp_path: Path) -> None:
    root = _package(tmp_path, _guarded("alpha", "beta"), _guarded("beta", "alpha"))
    assert not _closes_a_cycle(root, "alpha", "beta")
    assert not _closes_a_cycle(root, "beta", "alpha")


@pytest.mark.parametrize(
    "alpha",
    [
        # The else branch runs.
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    pass\n"
        "else:\n    from .beta import Beta\n",
        # A test that is not the name runs its body.
        "from typing import TYPE_CHECKING\n\nif not TYPE_CHECKING:\n    from .beta import Beta\n",
        # The module rebinds the name after importing it.
        "from typing import TYPE_CHECKING\n\nTYPE_CHECKING = True\n\n"
        "if TYPE_CHECKING:\n    from .beta import Beta\n",
        # A name no import binds is not typing's.
        "if TYPE_CHECKING:\n    from .beta import Beta\n",
        # A function's own TYPE_CHECKING shadows the module's.
        "from typing import TYPE_CHECKING\n\n\ndef late() -> None:\n    TYPE_CHECKING = True\n"
        "    if TYPE_CHECKING:\n        from .beta import Beta\n",
    ],
    ids=["else", "not", "rebound", "unbound", "shadowed-in-function"],
)
def test_a_cycle_through_an_import_that_may_run_is_still_one(tmp_path: Path, alpha: str) -> None:
    root = _package(tmp_path, alpha, _guarded("beta", "alpha"))
    assert _closes_a_cycle(root, "alpha", "beta")


@pytest.mark.parametrize(
    "alpha",
    [
        "import typing\n\nif typing.TYPE_CHECKING:\n    from .beta import Beta\n",
        "import typing_extensions as te\n\nif te.TYPE_CHECKING:\n    from .beta import Beta\n",
        "from typing import TYPE_CHECKING as CHECKING\n\nif CHECKING:\n    from .beta import Beta\n",
        "TYPE_CHECKING = False\n\nif TYPE_CHECKING:\n    from .beta import Beta\n",
        "try:\n    from typing import TYPE_CHECKING\nexcept ImportError:\n    TYPE_CHECKING = False\n"
        "\nif TYPE_CHECKING:\n    from .beta import Beta\n",
        "from typing import TYPE_CHECKING\n\n\nclass Holder:\n    if TYPE_CHECKING:\n"
        "        from .beta import Beta\n",
        "from typing import TYPE_CHECKING\n\n\ndef late() -> None:\n    if TYPE_CHECKING:\n"
        "        from .beta import Beta\n",
    ],
    ids=["module", "aliased-module", "aliased-name", "own-false", "try", "class", "function"],
)
def test_every_spelling_of_the_guard_hides_its_body(tmp_path: Path, alpha: str) -> None:
    root = _package(tmp_path, alpha, _guarded("beta", "alpha"))
    assert not _closes_a_cycle(root, "alpha", "beta")


def test_every_extent_skips_the_guarded_body_and_keeps_its_else() -> None:
    source = textwrap.dedent("""\
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            import checked
        else:
            import running


        class Holder:
            if TYPE_CHECKING:
                import checked_in_class


        def late():
            if TYPE_CHECKING:
                import checked_in_function
            import called
        """)
    tree = ast.parse(source)
    guards = TypeCheckingGuards.of(source, tree)

    def imported(extent: str) -> set[str]:
        return {
            alias.name
            for node in _import_statements(tree, extent, guards)  # type: ignore[arg-type]
            for alias in node.names
        } - {"TYPE_CHECKING"}

    assert imported("everywhere") == {"running", "called"}
    assert imported("at_import") == {"running"}


def test_a_requirement_in_the_guards_else_is_required(tmp_path: Path) -> None:
    """What a module requires at import: the ``else`` of the guard runs, its body does not."""
    module = tmp_path / "module.py"
    module.write_text(
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import checked\n"
        "else:\n    import tornado\n"
    )
    assert _required_imports(module, ImportGraphCache()) == frozenset({"typing", "tornado"})


def _dry(root: Path, *, types: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            ".",
            ".",
            "--no-interactive",
            *(() if types else ("--no-types",)),
            "--no-format",
            "--cross-module",
            "--progress",
            "none",
        ],
        capture_output=True,
        text=True,
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )


def imports_in_every_order(root: Path, modules: list[str], call: str) -> None:
    """Each module imported first, in a fresh interpreter, then the rest; all must load and run."""
    for first in modules:
        rest = [module for module in modules if module != first]
        script = "".join(f"import {module}\n" for module in [first, *rest]) + call
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys\nsys.path.insert(0, {str(root)!r})\n" + script,
            ],
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
            timeout=60,
        )
        assert completed.returncode == 0, (first, completed.stderr)


def test_modules_that_name_each_other_for_the_checker_share_a_helper(tmp_path: Path) -> None:
    root = _package(tmp_path, _guarded("alpha", "beta"), _guarded("beta", "alpha"))
    result = _dry(root)
    assert result.returncode == 0, result.stdout + result.stderr
    alpha = (root / "pkg" / "alpha.py").read_text()
    beta = (root / "pkg" / "beta.py").read_text()
    assert "__extracted_func" in alpha and "__extracted_func" in beta, alpha + beta
    assert ("from .alpha import" in beta) != ("from .beta import __extracted" in alpha)
    imports_in_every_order(
        root,
        ["pkg.alpha", "pkg.beta"],
        "from pkg.alpha import fa\nfrom pkg.beta import fb\nprint(fa([0, 2, 3]), fb([1, 5]))\n",
    )


_ERROR = """\
class {name}(Exception):
    def __init__(self, message: str, context: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:
        if self.context:
            return f"{{self.message}} in {{self.context!r}}"
        return self.message
"""

_SUMMARY = """

def summary_{name}(values: list[int]) -> int:
    total = 0
    for value in values:
        if value > 1:
            total += value * 2
        else:
            total -= value
    return total + 1
"""


@requires_mypy
def test_a_type_only_import_towel_wrote_refuses_no_later_pair(tmp_path: Path) -> None:
    """mistune's renderers, in miniature: the second pair between two modules is still shared.

    The first extraction hosts a generic helper in direct.py whose type
    variable ranges over lock.py's class too, imported under ``TYPE_CHECKING``,
    while lock.py imports the helper. A later duplicate between the same two
    modules then had no host: in lock.py it closes a real cycle, and in
    direct.py the guard followed the type-only import back to lock.py.
    """
    files: Mapping[str, str] = {
        "pyproject.toml": "[tool.mypy]\nstrict = true\n",
        "tests/test_errors.py": "from pkg.direct import DirectError\nfrom pkg.lock import LockError\n",
        "pkg/__init__.py": "",
        "pkg/direct.py": _ERROR.format(name="DirectError"),
        "pkg/lock.py": _ERROR.format(name="LockError"),
    }
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    first = _dry(tmp_path, types=True)
    assert first.returncode == 0, first.stdout + first.stderr
    direct = (tmp_path / "pkg" / "direct.py").read_text()
    assert (
        "if _typing.TYPE_CHECKING:  # pragma: no cover\n    from .lock import LockError as _LockError"
        in direct
    ), direct
    for module in ("direct", "lock"):
        with (tmp_path / "pkg" / f"{module}.py").open("a") as handle:
            handle.write(_SUMMARY.format(name=module))
    second = _dry(tmp_path, types=True)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Extract common code from summary_direct" in second.stdout, second.stdout
    assert (
        "def summary_lock(values: list[int]) -> int:\n    return "
        in (tmp_path / "pkg" / "lock.py").read_text()
    )
    imports_in_every_order(
        tmp_path,
        ["pkg.direct", "pkg.lock"],
        "from pkg.direct import DirectError, summary_direct\n"
        "from pkg.lock import LockError, summary_lock\n"
        "assert str(DirectError('m', 'c')) == \"m in 'c'\" and str(LockError('m')) == 'm'\n"
        "assert summary_direct([0, 2, 3]) == summary_lock([0, 2, 3]) == 11\n",
    )
