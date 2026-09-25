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

"""What the import model says about a name holds wherever the program runs.

The class of defect the third audit kept finding: Towel names a module from
evidence it cannot see where the program runs. A directory named like a library
the project requires (P1-2), a library or the project itself installed in the
project's own ``.venv`` (D3), a host the build leaves out (D5), a module a file
imports by a top-level name from inside its package (P1-6). Each layout below is
one of those, or a control, with the ``sys.path`` of every place it runs: the
source tree, the installed wheel beside its libraries, the project's venv. For
each, the model is asked with the interpreter Towel would run in, and every
verdict is checked against ``importlib.util.find_spec`` in one subprocess:

- a name the model trusts resolves, in every runtime, to the tree's own copy;
- a spelling the model offers, from an importer running under a known name,
  resolves there to the provider.

Every file is marked with where it comes from (``# tree:<path>``, or a library
or installed copy), so a resolution is compared by what it found, not by where.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Mapping, Optional, Tuple

import pytest

from towel.import_model import ImportModel, build_import_model, installed_outside

_SITE = "lib/site-packages"


@dataclass(frozen=True)
class Runtime:
    """One place the program runs: its ``sys.path``, and modules it runs by a name of their own."""

    path: Tuple[str, ...]
    """Directories relative to the layout, in order."""
    runs: Tuple[Tuple[str, str], ...] = ()
    """``(name, tree file)`` for a module run under a name the model does not give it, as a script."""


@dataclass(frozen=True)
class Layout:
    name: str
    tree: Mapping[str, str]
    """The project, under ``project/``; each ``.py`` file is marked as the tree's own."""
    elsewhere: Mapping[str, str] = field(default_factory=dict)
    """Libraries and installed copies, relative to the layout; each ``.py`` file is marked by its place."""
    shipped: Tuple[str, ...] = ()
    """Tree files the wheel holds, copied under ``wheel/`` below their package root."""
    wheel_root: str = ""
    """The directory of the tree the wheel's packages sit in, ``src`` for a src layout."""
    probe: Tuple[str, ...] = ()
    """``sys.path`` entries, relative to the layout, the interpreter Towel runs in adds."""
    runtimes: Tuple[Runtime, ...] = ()


LAYOUTS = (
    Layout(
        "namesake-of-a-required-library",
        {
            "pyproject.toml": '[project]\nname = "zz1app"\ndependencies = ["zz1lib>=8"]\n',
            "zz1app/__init__.py": "",
            "zz1app/core.py": "from zz1lib.utils import echo\n",
            "zz1lib/__init__.py": "",
            "zz1lib/utils.py": "def echo(x):\n    return x\n",
        },
        elsewhere={"site/zz1lib/__init__.py": "", "site/zz1lib/utils.py": ""},
        shipped=("zz1app/__init__.py", "zz1app/core.py"),
        runtimes=(Runtime(("wheel", "site")), Runtime(("project",))),
    ),
    Layout(
        "a-directory-that-is-the-projects-own",
        {
            "pyproject.toml": '[project]\nname = "zz2app"\n',
            "zz2app/__init__.py": "",
            "zz2app/core.py": "from zz2lib.utils import echo\n",
            "zz2lib/__init__.py": "",
            "zz2lib/utils.py": "",
        },
        shipped=("zz2app/__init__.py", "zz2app/core.py", "zz2lib/__init__.py", "zz2lib/utils.py"),
        runtimes=(Runtime(("wheel",)), Runtime(("project",))),
    ),
    Layout(
        "a-library-in-the-projects-venv",
        {
            "zz3shadow/a.py": "",
            "zz3app/__init__.py": "",
            "zz3app/x.py": "from zz3shadow.b import other\n",
            "zz3app/y.py": "from zz3shadow.a import thing\n",
            ".venv/pyvenv.cfg": "home = /x\n",
        },
        elsewhere={
            f"project/.venv/{_SITE}/zz3shadow/__init__.py": "",
            f"project/.venv/{_SITE}/zz3shadow/a.py": "",
            f"project/.venv/{_SITE}/zz3shadow/b.py": "",
        },
        probe=(f"project/.venv/{_SITE}",),
        runtimes=(Runtime(("project", f"project/.venv/{_SITE}")),),
    ),
    Layout(
        "the-project-installed-in-its-venv",
        {
            "src/zz4shop/__init__.py": "",
            "src/zz4shop/util.py": "",
            "src/zz4shop/orders.py": "",
            "tests/test_shop.py": "from zz4shop.util import describe\nfrom zz4shop.orders import total\n",
            ".venv/pyvenv.cfg": "home = /x\n",
        },
        elsewhere={
            f"project/.venv/{_SITE}/zz4shop/__init__.py": "",
            f"project/.venv/{_SITE}/zz4shop/util.py": "",
            f"project/.venv/{_SITE}/zz4shop/orders.py": "",
        },
        probe=(f"project/.venv/{_SITE}",),
        runtimes=(Runtime(("project/tests", f"project/.venv/{_SITE}")),),
    ),
    Layout(
        "the-project-installed-editable-in-its-venv",
        {
            "src/zz5shop/__init__.py": "",
            "src/zz5shop/util.py": "",
            "src/zz5shop/orders.py": "from .util import x\n",
            "tests/test_shop.py": "from zz5shop.util import describe\nfrom zz5shop.orders import total\n",
            ".venv/pyvenv.cfg": "home = /x\n",
        },
        elsewhere={f"project/.venv/{_SITE}/unrelated.py": ""},
        probe=("project/src", f"project/.venv/{_SITE}"),
        runtimes=(Runtime(("project/tests", "project/src", f"project/.venv/{_SITE}")),),
    ),
    Layout(
        "a-stale-build-copy",
        {
            "src/zz6/__init__.py": "",
            "src/zz6/a.py": "",
            "build/lib/zz6/__init__.py": "",
            "tests/test_a.py": "import zz6.a\n",
        },
        runtimes=(Runtime(("project/tests", "project/src")), Runtime(("project/build/lib",))),
    ),
    Layout(
        "a-host-hatch-leaves-out",
        {
            "pyproject.toml": '[tool.hatch.build.targets.wheel]\npackages = ["src/zz7shop"]\n'
            'exclude = ["src/zz7shop/_devtools.py"]\n',
            "src/zz7shop/__init__.py": "",
            "src/zz7shop/stats.py": "",
            "src/zz7shop/_devtools.py": "",
            "tests/test_shop.py": "import zz7shop.stats\n",
        },
        shipped=("src/zz7shop/__init__.py", "src/zz7shop/stats.py"),
        wheel_root="src",
        runtimes=(Runtime(("wheel",)), Runtime(("project/src",))),
    ),
    Layout(
        "a-subpackage-setuptools-leaves-out",
        {
            "pyproject.toml": '[tool.setuptools.packages.find]\nwhere = ["src"]\nexclude = ["zz8shop.devtools*"]\n',
            "src/zz8shop/__init__.py": "",
            "src/zz8shop/stats.py": "",
            "src/zz8shop/cli.py": "def main():\n    from .devtools.dump import dump\n    return dump\n",
            "src/zz8shop/devtools/__init__.py": "",
            "src/zz8shop/devtools/dump.py": "",
            "tests/test_shop.py": "import zz8shop.stats\n",
        },
        shipped=("src/zz8shop/__init__.py", "src/zz8shop/stats.py", "src/zz8shop/cli.py"),
        wheel_root="src",
        runtimes=(Runtime(("wheel",)), Runtime(("project/src",))),
    ),
    Layout(
        "a-top-level-module-inside-its-package",
        {
            "zz9/__init__.py": "",
            "zz9/a.py": "",
            "zz9/c.py": "import zz9helpers\n",
            "zz9/zz9helpers.py": "",
            "tests/__init__.py": "",
            "tests/test_x.py": "import zz9.a\n",
        },
        runtimes=(
            Runtime(("project",)),
            Runtime(("project/zz9",), runs=(("c", "zz9/c.py"),)),
        ),
    ),
    Layout(
        "a-script-beside-the-module-it-imports",
        {
            "zz11scripts/__init__.py": "",
            "zz11scripts/run.py": "import zz11helpers\n",
            "zz11scripts/zz11helpers.py": "",
        },
        runtimes=(Runtime(("project/zz11scripts",), runs=(("run", "zz11scripts/run.py"),)),),
    ),
    Layout(
        "a-src-layout",
        {
            "src/zz10/__init__.py": "from . import b\n",
            "src/zz10/a.py": "from .b import VALUE\n",
            "src/zz10/b.py": "VALUE = 1\n",
            "tests/test_a.py": "from zz10.a import VALUE\n",
        },
        shipped=("src/zz10/__init__.py", "src/zz10/a.py", "src/zz10/b.py"),
        wheel_root="src",
        runtimes=(Runtime(("project/tests", "project/src")), Runtime(("wheel",))),
    ),
)


def _marked(text: str, marker: str) -> str:
    return f"# {marker}\n{text}"


def _build(directory: Path, layout: Layout) -> None:
    for name, text in layout.tree.items():
        path = directory / "project" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_marked(text, f"tree:{name}") if name.endswith(".py") else text)
    for name, text in layout.elsewhere.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_marked(text, f"elsewhere:{name}"))
    for name in layout.shipped:
        relative = Path(name).relative_to(layout.wheel_root) if layout.wheel_root else Path(name)
        target = directory / "wheel" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((directory / "project" / name).read_bytes())


def _model(directory: Path, layout: Layout, monkeypatch: pytest.MonkeyPatch) -> ImportModel:
    """The model as the interpreter Towel runs in would build it, with the layout's entries on its path."""
    with monkeypatch.context() as patched:
        patched.setattr(sys, "path", [str(directory / entry) for entry in layout.probe] + sys.path)
        importlib.invalidate_caches()
        return build_import_model(directory / "project", installed=installed_outside)


@dataclass(frozen=True)
class _Claim:
    """What one verdict says ``find_spec`` finds in one runtime."""

    layout: str
    runtime: int
    module: str
    expected: Optional[str]
    """The marker ``module`` must resolve to; ``None`` for any tree file, or nothing."""
    because: str
    only_if: Optional[Tuple[str, str]] = None
    """``(module, marker)``: the claim stands only where that lookup finds that marker."""


def _marker(root: Path, path: Path) -> str:
    return f"tree:{path.relative_to(root).as_posix()}"


def _claims(layout: Layout, model: ImportModel) -> List[_Claim]:
    root = model.root
    claims = [
        _Claim(layout.name, index, name, None, f"{name} is trusted")
        for name, info in model.names.items()
        if info.trusted and name.startswith("zz")
        for index, _ in enumerate(layout.runtimes)
    ]
    modules = sorted(path for path in root.rglob("*.py") if ".venv" not in path.parts)
    for importer in modules:
        for provider in modules:
            spelling = model.spelling(importer, provider)
            if spelling is None:
                continue
            for index, runtime in enumerate(layout.runtimes):
                scripts = [name for name, file in runtime.runs if root / file == importer]
                own = model.module_name(importer)
                running: List[Tuple[str, Optional[Tuple[str, str]]]] = [
                    (name, None) for name in scripts
                ] or ([(own, (own, _marker(root, importer)))] if own is not None else [])
                for name, only_if in running:
                    package = name if importer.name == "__init__.py" else name.rpartition(".")[0]
                    try:
                        absolute = importlib.util.resolve_name(spelling.module, package)
                    except ImportError:
                        absolute = "<no module: a relative import in a top-level module>"
                    because = (
                        f"{importer.relative_to(root)} running as {name} imports {spelling.module}"
                    )
                    claims.append(
                        _Claim(
                            layout.name, index, absolute, _marker(root, provider), because, only_if
                        )
                    )
    return claims


_FINDER = """
import importlib, importlib.util, json, sys
queries = json.load(sys.stdin)
answers = []
base = list(sys.path)
for path, module in queries:
    for name in [name for name in sys.modules if name.startswith("zz") or name == "c"]:
        del sys.modules[name]
    sys.path[:] = path + base
    importlib.invalidate_caches()
    try:
        spec = None if module.startswith("<") else importlib.util.find_spec(module)
    except Exception:
        spec = None
    marker = None
    if spec is not None and spec.origin not in (None, "built-in", "frozen"):
        with open(spec.origin, encoding="utf-8") as stream:
            marker = stream.readline()[2:].strip()
    answers.append(marker)
json.dump(answers, sys.stdout)
"""


def test_every_verdict_holds_where_the_program_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims: List[_Claim] = []
    paths: Dict[Tuple[str, int], List[str]] = {}
    for layout in LAYOUTS:
        directory = tmp_path / layout.name
        _build(directory, layout)
        claims.extend(_claims(layout, _model(directory, layout, monkeypatch)))
        for index, runtime in enumerate(layout.runtimes):
            paths[(layout.name, index)] = [str(directory / entry) for entry in runtime.path]
    lookups = sorted(
        {(claim.layout, claim.runtime, claim.module) for claim in claims}
        | {
            (claim.layout, claim.runtime, claim.only_if[0])
            for claim in claims
            if claim.only_if is not None
        }
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _FINDER],
        input=json.dumps([[paths[(layout, index)], module] for layout, index, module in lookups]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    found = dict(zip(lookups, json.loads(result.stdout)))

    def holds(claim: _Claim) -> bool:
        if claim.only_if is not None:
            module, marker = claim.only_if
            if found[(claim.layout, claim.runtime, module)] != marker:
                return True  # The importer does not run under that name there.
        answer: Optional[str] = found[(claim.layout, claim.runtime, claim.module)]
        if claim.expected is None:
            return answer is None or answer.startswith("tree:")
        return answer == claim.expected

    broken = [
        f"{claim.layout} (runtime {claim.runtime}): {claim.because},"
        f" but {claim.module} is {found[(claim.layout, claim.runtime, claim.module)]}"
        for claim in claims
        if not holds(claim)
    ]
    assert broken == [], "\n".join(broken)
    # The controls are judged, so the property is not vacuous.
    judged = {claim.layout for claim in claims}
    assert {
        "a-directory-that-is-the-projects-own",
        "the-project-installed-editable-in-its-venv",
        "a-src-layout",
    } <= judged
