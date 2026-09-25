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

"""What each backend puts in the wheel, as its own build recorded it, against ``shipped_files``.

Round 4 found Poetry's ``packages`` globs and PDM's ``includes`` unread, so
a helper went to ``shop/_devtools.py``, which neither wheel holds, and the
installed ``shop.stats`` raised ``ModuleNotFoundError``. Each case below was
built offline, straight to a wheel and through its sdist, by poetry-core
2.5.0, pdm-backend 2.4.10, hatchling 1.32.4, flit_core 4.1.0, setuptools 84.0.0
and uv 0.11.2's build backend; the modules both wheels held are recorded.
Every module ``left_out`` does not leave out of the wheel must be among them,
so a reading that lets a missing module host fails here, and on these trees
it leaves out nothing that ships.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Mapping

import pytest

from towel.import_model import build_import_model
from towel.shipped_files import Artifact, left_out

_PY = "x = 1\n"
_SRC = {
    "src/shop/__init__.py": "",
    "src/shop/stats.py": _PY,
    "src/shop/_devtools.py": _PY,
    "src/shop/sub/__init__.py": "",
    "src/shop/sub/m.py": _PY,
    "src/shop/ns/n.py": _PY,
    "src/other/__init__.py": "",
    "src/other/o.py": _PY,
    "src/helper.py": _PY,
    "tests/__init__.py": "",
    "tests/test_x.py": _PY,
}
_FLAT = {name.removeprefix("src/"): text for name, text in _SRC.items()}

# (case, tree, build backend, the rest of pyproject.toml, what both wheels held)
_RECORDED = [
    (
        "flit_default",
        _FLAT,
        "flit_core.buildapi",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n',
        [
            "shop/__init__.py",
            "shop/_devtools.py",
            "shop/ns/n.py",
            "shop/stats.py",
            "shop/sub/__init__.py",
            "shop/sub/m.py",
        ],
    ),
    (
        "flit_module",
        _FLAT,
        "flit_core.buildapi",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.flit.module]\nname = "other"\n',
        ["other/__init__.py", "other/o.py"],
    ),
    (
        "hatch_default",
        _SRC,
        "hatchling.build",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n',
        [
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "hatch_only_packages",
        _SRC,
        "hatchling.build",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.hatch.build.targets.wheel]\npackages = ["src/shop"]\nonly-packages = true\n',
        [
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "pdm_default_flat",
        _FLAT,
        "pdm.backend",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n',
        [
            "other/__init__.py",
            "other/o.py",
            "shop/__init__.py",
            "shop/_devtools.py",
            "shop/ns/n.py",
            "shop/stats.py",
            "shop/sub/__init__.py",
            "shop/sub/m.py",
        ],
    ),
    (
        "pdm_default_src",
        _SRC,
        "pdm.backend",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n',
        [
            "src/other/__init__.py",
            "src/other/o.py",
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "pdm_dir_excludes",
        _SRC,
        "pdm.backend",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.pdm.build]\nincludes = ["src/shop"]\nexcludes = ["src/shop/_devtools.py", "**/sub"]\n',
        ["src/shop/__init__.py", "src/shop/ns/n.py", "src/shop/stats.py"],
    ),
    (
        "pdm_includes",
        _SRC,
        "pdm.backend",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.pdm.build]\npackage-dir = "src"\nincludes = ["src/shop/__init__.py", "src/shop/stats.py"]\n',
        ["src/shop/__init__.py", "src/shop/stats.py"],
    ),
    (
        "pdm_source_includes",
        _FLAT,
        "pdm.backend",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.pdm.build]\nsource-includes = ["other"]\n',
        [
            "shop/__init__.py",
            "shop/_devtools.py",
            "shop/ns/n.py",
            "shop/stats.py",
            "shop/sub/__init__.py",
            "shop/sub/m.py",
            "tests/__init__.py",
            "tests/test_x.py",
        ],
    ),
    (
        "poetry_default",
        _SRC,
        "poetry.core.masonry.api",
        '[tool.poetry]\nname = "shop"\nversion = "0.1"\ndescription = ""\nauthors = ["x <x@example.com>"]\n',
        [
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "poetry_formats",
        _SRC,
        "poetry.core.masonry.api",
        '[tool.poetry]\nname = "shop"\nversion = "0.1"\ndescription = ""\nauthors = ["x <x@example.com>"]\npackages = [{ include = "shop", from = "src" }, { include = "other", from = "src", format = "sdist" }]\ninclude = [{ path = "src/helper.py", format = "wheel" }]\n',
        [
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "poetry_glob",
        _SRC,
        "poetry.core.masonry.api",
        '[tool.poetry]\nname = "shop"\nversion = "0.1"\ndescription = ""\nauthors = ["x <x@example.com>"]\npackages = [{ include = "shop/[!_]*.py", from = "src" }]\n',
        ["src/shop/stats.py"],
    ),
    (
        "poetry_pep621",
        _SRC,
        "poetry.core.masonry.api",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.poetry]\npackages = [{ include = "shop", from = "src" }, { include = "other", from = "src" }]\n',
        [
            "src/other/__init__.py",
            "src/other/o.py",
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "poetry_pkg_exclude",
        _SRC,
        "poetry.core.masonry.api",
        '[tool.poetry]\nname = "shop"\nversion = "0.1"\ndescription = ""\nauthors = ["x <x@example.com>"]\npackages = [{ include = "shop", from = "src" }]\nexclude = ["src/shop/_devtools.py"]\n',
        [
            "src/shop/__init__.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "setuptools_find",
        _SRC,
        "setuptools.build_meta",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        [
            "src/other/__init__.py",
            "src/other/o.py",
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "setuptools_py_modules",
        _SRC,
        "setuptools.build_meta",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.setuptools]\npackage-dir = {"" = "src"}\npy-modules = ["helper"]\n',
        ["src/helper.py"],
    ),
    (
        "setuptools_py_modules_find",
        _SRC,
        "setuptools.build_meta",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.setuptools]\npy-modules = ["helper"]\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        [
            "src/helper.py",
            "src/other/__init__.py",
            "src/other/o.py",
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "uv_default",
        _SRC,
        "uv_build",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n',
        [
            "src/shop/__init__.py",
            "src/shop/_devtools.py",
            "src/shop/ns/n.py",
            "src/shop/stats.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/m.py",
        ],
    ),
    (
        "uv_module_name",
        _FLAT,
        "uv_build",
        '[project]\nname = "shop"\nversion = "0.1"\ndescription = "d"\n[tool.uv.build-backend]\nmodule-name = "other"\nmodule-root = ""\n',
        ["other/__init__.py", "other/o.py"],
    ),
]


@pytest.mark.parametrize(
    "tree, backend, configuration, shipped",
    [case[1:] for case in _RECORDED],
    ids=[case[0] for case in _RECORDED],
)
def test_the_wheel_holds_every_module_not_left_out(
    tmp_path: Path, tree: Mapping[str, str], backend: str, configuration: str, shipped: List[str]
) -> None:
    files = {
        **tree,
        "pyproject.toml": f'[build-system]\nbuild-backend = "{backend}"\n{configuration}',
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    root = tmp_path.resolve()
    modules = sorted(path.resolve() for path in root.rglob("*.py"))
    found = left_out(root, modules)
    kept = sorted(
        module.relative_to(root).as_posix()
        for module in modules
        if Artifact.WHEEL not in found.get(module, frozenset())
    )
    assert kept == sorted(shipped)


@pytest.mark.parametrize(
    "configuration",
    [
        '[tool.poetry]\nname = "shop"\nversion = "0.1"\n'
        'packages = [{ include = "shop/[!_]*.py", from = "src" }]\n',
        '[project]\nname = "shop"\nversion = "0.1"\n[tool.pdm.build]\npackage-dir = "src"\n'
        'includes = ["src/shop/__init__.py", "src/shop/stats.py"]\n',
    ],
    ids=["poetry-packages-glob", "pdm-includes"],
)
def test_r9xh_a_module_the_wheel_omits_hosts_nothing_for_one_it_keeps(
    tmp_path: Path, configuration: str
) -> None:
    for name, text in {
        "pyproject.toml": configuration,
        "src/shop/__init__.py": "",
        "src/shop/stats.py": _PY,
        "src/shop/_devtools.py": _PY,
        "tests/test_shop.py": "import shop.stats\n",
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    model = build_import_model(tmp_path, installed=lambda name, root: None)
    shop = model.root / "src" / "shop"
    assert model.spelling(shop / "stats.py", shop / "_devtools.py") is None
    kept = model.spelling(shop / "_devtools.py", shop / "stats.py")
    assert kept is not None and kept.module == ".stats"
