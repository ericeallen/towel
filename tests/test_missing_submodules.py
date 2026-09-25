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

"""A missing submodule is missing however the import spells it.

The third audit's P2-4 (semantic): ``from .generated_at_build import VERSION``
was reported and its file left unchanged, as decided, but ``from . import
generated_at_build`` and ``from pkg import generated_at_build`` were taken for
imports of an attribute of the package, and their file borrowed a helper. A
name the package's initializer does not bind, and no module there provides,
is now a module the tree lacks in every spelling. An initializer that may bind
names no statement shows leaves every name possible.
"""

from __future__ import annotations

from pathlib import Path
import sys
import textwrap
from typing import Mapping, Optional

import pytest

from towel.import_model import (
    OutsideProvider,
    RelativeImportMissing,
    UnresolvedImport,
    build_import_model,
    installed_outside,
)


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


_PACKAGE = {
    # The project is the distribution zzpkg, so no other copy holds what zzpkg lacks.
    "pyproject.toml": '[project]\nname = "zzpkg"\n',
    "zzpkg/__init__.py": "",
    "zzpkg/a.py": "",
    "zzpkg/sub/__init__.py": "",
    "tests/test_a.py": "import zzpkg.a\n",
}


@pytest.mark.parametrize(
    "importer, statement, missing",
    [
        ("zzpkg/c.py", "from . import generated_at_build", ".generated_at_build"),
        ("zzpkg/c.py", "from .sub import generated_at_build", ".sub.generated_at_build"),
        ("zzpkg/sub/c.py", "from .. import a, generated_at_build", "..generated_at_build"),
        ("zzpkg/c.py", "from zzpkg import generated_at_build", "zzpkg.generated_at_build"),
        ("zzpkg/c.py", "from zzpkg.sub import generated_at_build", "zzpkg.sub.generated_at_build"),
        ("tests/test_b.py", "from zzpkg import a, generated_at_build", "zzpkg.generated_at_build"),
    ],
)
def test_every_spelling_of_a_missing_submodule_is_reported(
    tmp_path: Path, importer: str, statement: str, missing: str
) -> None:
    root = _write(tmp_path, {**_PACKAGE, importer: statement + "\n"})
    model = build_import_model(root, installed=_standard_library_only)
    (problem,) = model.problems
    assert isinstance(problem, (RelativeImportMissing, UnresolvedImport))
    assert problem.missing == missing
    assert model.importers_of_missing_modules == {model.root / importer}
    assert model.spelling(model.root / importer, model.root / "zzpkg/a.py") is None


@pytest.mark.parametrize(
    "initializer",
    [
        "generated_at_build = None\n",
        "from .a import *\n",
        "def __getattr__(name):\n    return name\n",
        "globals()['generated_at_build'] = 1\n",
        "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n",
        "import sys\nsys.modules[__name__ + '.generated_at_build'] = sys\n",
        "try:\n    from ._fast import generated_at_build\nexcept ImportError:\n    generated_at_build = None\n",
        "if True:\n    for generated_at_build in range(1):\n        pass\n",
        "from . import a as generated_at_build\n",
        # bs4's builder registers its tree builders on itself from a function.
        "import sys\ndef register(name):\n    setattr(sys.modules[__name__], name, 1)\n",
    ],
    ids=[
        "bound",
        "star",
        "module-getattr",
        "globals",
        "path",
        "registers",
        "guarded",
        "loop",
        "submodule-as",
        "setattr-in-a-function",
    ],
)
def test_a_name_the_initializer_binds_or_may_bind_is_no_missing_module(
    tmp_path: Path, initializer: str
) -> None:
    root = _write(
        tmp_path,
        {
            **_PACKAGE,
            "zzpkg/__init__.py": initializer,
            "zzpkg/c.py": "from . import generated_at_build\n",
        },
    )
    model = build_import_model(root, installed=_standard_library_only)
    assert not [
        p for p in model.problems if isinstance(p, (RelativeImportMissing, UnresolvedImport))
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"zzpkg/generated_at_build.py": ""},
        {"zzpkg/generated_at_build/__init__.py": ""},
        {"zzpkg/generated_at_build.pyi": ""},
        {"zzpkg/generated_at_build.cpython-312-darwin.so": ""},
    ],
    ids=["module", "package", "stub", "extension"],
)
def test_a_submodule_that_exists_is_no_missing_module(
    tmp_path: Path, extra: Mapping[str, str]
) -> None:
    root = _write(
        tmp_path, {**_PACKAGE, **extra, "zzpkg/c.py": "from . import generated_at_build\n"}
    )
    assert build_import_model(root, installed=_standard_library_only).problems == ()


def test_a_namespace_package_binds_nothing_but_its_modules(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "zzns"\n',
            "zzns/x.py": "",
            "tests/test_a.py": "import zzns.x\nfrom zzns import generated_at_build\n",
        },
    )
    model = build_import_model(root, installed=_standard_library_only)
    (problem,) = model.problems
    assert isinstance(problem, UnresolvedImport) and problem.missing == "zzns.generated_at_build"


def test_the_initializers_own_import_of_its_submodule_binds_nothing(tmp_path: Path) -> None:
    """``from . import generated_at_build`` in ``__init__.py`` fails where the module is missing."""
    root = _write(tmp_path, {**_PACKAGE, "zzpkg/__init__.py": "from . import generated_at_build\n"})
    model = build_import_model(root, installed=_standard_library_only)
    (problem,) = model.problems
    assert isinstance(problem, RelativeImportMissing) and problem.missing == ".generated_at_build"


_TYPE_ONLY = (
    "import zzpkg.a\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    {statement}\n"
)


@pytest.mark.parametrize(
    "importer, statement",
    [
        ("zzpkg/c.py", "from ._generated import Row"),
        ("zzpkg/c.py", "from . import generated_at_build"),
        ("zzpkg/c.py", "from zzpkg._generated import Row"),
        ("tests/test_b.py", "from zzpkg._generated import Row"),
    ],
)
def test_a_type_only_import_of_a_missing_module_leaves_its_file_free(
    tmp_path: Path, importer: str, statement: str
) -> None:
    """It never runs, so it is no import edge, and its file loads wherever it did (round-4 P2)."""
    root = _write(
        tmp_path,
        {**_PACKAGE, importer: _TYPE_ONLY.format(statement=statement)},
    )
    model = build_import_model(root, installed=_standard_library_only)
    assert model.problems == ()
    assert model.importers_of_missing_modules == frozenset()
    assert model.spelling(model.root / importer, model.root / "zzpkg/a.py") is not None


def test_a_type_only_import_that_climbs_out_of_its_package_is_still_reported(
    tmp_path: Path,
) -> None:
    """A checker resolving it sees a larger package than the tree shows, so the name is in doubt."""
    root = _write(
        tmp_path, {**_PACKAGE, "zzpkg/c.py": _TYPE_ONLY.format(statement="from ... import x")}
    )
    (problem,) = build_import_model(root, installed=_standard_library_only).problems
    assert type(problem).__name__ == "RelativeImportEscapes"
