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

"""Resolving a module's callees to the typing forms they may denote.

Each spelling is resolved the way its module binds it: through the module's
globals on every path, the imports of its function bodies, star imports, and
into the project's own modules for a re-export. Where a binding cannot be
followed, a spelling whose last name is a form's is taken to be that form,
which can only pin more; an import of a module outside the project is taken
at its word.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, FrozenSet, Tuple

import pytest

from tests.test_helpers import write_file
from towel.unification.import_graph import ImportGraphCache
from towel.unification.static_positions import (
    DEFAULT_TRANSLATION_KEYWORDS,
    TYPING_FORM_NAMES,
    Pin,
    statically_read,
)
from towel.unification.typing_forms import (
    FORM_ORIGINS,
    ModuleText,
    TypingFormResolver,
    typing_forms_of,
)

PYPROJECT = '[project]\nname = "sample"\nversion = "0"\n'


def _forms(tmp_path: Path, files: Dict[str, str], module: str, spelling: str) -> FrozenSet[str]:
    write_file(tmp_path / "pyproject.toml", PYPROJECT)
    for name, text in files.items():
        write_file(tmp_path / name, text)
    path = tmp_path / module
    resolver = TypingFormResolver(ModuleText(str(path), path.read_text()), ImportGraphCache())
    return resolver.forms_of(spelling)


# The module's source, a spelling it uses, and the forms that spelling may denote.
ONE_MODULE: Dict[str, Tuple[str, str, FrozenSet[str]]] = {
    "an alias of an imported form": (
        "from typing import TypeVar as TV\n",
        "TV",
        frozenset({"TypeVar"}),
    ),
    "a form through the typing module": ("import typing as t\n", "t.cast", frozenset({"cast"})),
    "anything else of the typing module": ("import typing as t\n", "t.Any", frozenset()),
    "a form of typing_extensions": (
        "import typing_extensions\n",
        "typing_extensions.Literal",
        frozenset({"Literal"}),
    ),
    "a form bound to a plain alias": (
        "import typing\ncheck = typing.assert_type\n",
        "check",
        frozenset({"assert_type"}),
    ),
    "a form a function body imports": (
        "def run():\n    from typing import cast as c\n    return c\n",
        "c",
        frozenset({"cast"}),
    ),
    "a form a function body aliases": (
        "import typing\n\n\ndef run():\n    c = typing.cast\n    return c\n",
        "c",
        frozenset({"cast"}),
    ),
    "a form bound on one path of two": (
        "try:\n    from typing import Literal\nexcept ImportError:\n    Literal = None\n",
        "Literal",
        frozenset({"Literal"}),
    ),
    "a form from a star import of typing": ("from typing import *\n", "cast", frozenset({"cast"})),
    "a name spelled as a form under a star import from elsewhere": (
        "from sqlalchemy import *\n",
        "cast",
        frozenset({"cast"}),
    ),
    "a module's own function named as a form": (
        "def cast(expression, to):\n    return expression\n",
        "cast",
        frozenset(),
    ),
    "a name the module never binds": ("import typing\n", "cast", frozenset()),
    "an import of another project's function named as a form": (
        "from sqlalchemy import cast\n",
        "cast",
        frozenset(),
    ),
    "an attribute of another project's module named as a form": (
        "from sqlglot import exp\n",
        "exp.cast",
        frozenset(),
    ),
    "a receiver's method named as a form": (
        "import typing\n",
        "self.cast",
        frozenset(),
    ),
    "a module that does not parse": ("def (:\n", "cast", frozenset({"cast"})),
}


@pytest.mark.parametrize("case", sorted(ONE_MODULE))
def test_a_spelling_denotes_what_its_module_binds_it_to(tmp_path: Path, case: str) -> None:
    source, spelling, expected = ONE_MODULE[case]
    assert _forms(tmp_path, {"sample.py": source}, "sample.py", spelling) == expected


# A package's other modules, ``pkg/sample.py``, a spelling it uses, and the
# forms that spelling may denote.
PACKAGES: Dict[str, Tuple[Dict[str, str], str, str, FrozenSet[str]]] = {
    "a form a compat module re-exports": (
        {"pkg/compat.py": "from typing_extensions import Literal\n"},
        "from .compat import Literal\n",
        "Literal",
        frozenset({"Literal"}),
    ),
    "a form a compat module re-exports, through the module": (
        {"pkg/compat.py": "from typing import cast as cast\n"},
        "from . import compat\n",
        "compat.cast",
        frozenset({"cast"}),
    ),
    "a form re-exported absolutely": (
        {"pkg/compat.py": "import typing\ncast = typing.cast\n"},
        "from pkg.compat import cast\n",
        "cast",
        frozenset({"cast"}),
    ),
    "the project's own function named as a form": (
        {"pkg/compat.py": "def cast(kind, value):\n    return value\n"},
        "from .compat import cast\n",
        "cast",
        frozenset(),
    ),
    "a conditional import, which cannot be followed": (
        {"pkg/compat.py": "def cast(kind, value):\n    return value\n"},
        "try:\n    from .compat import cast\nexcept ImportError:\n    pass\n",
        "cast",
        frozenset({"cast"}),
    ),
    "a re-export that never reaches a definition": (
        {"pkg/compat.py": "from .sample import cast\n"},
        "from .compat import cast\n",
        "cast",
        frozenset({"cast"}),
    ),
}


@pytest.mark.parametrize("case", sorted(PACKAGES))
def test_an_import_of_the_project_is_followed_to_what_it_binds(tmp_path: Path, case: str) -> None:
    others, source, spelling, expected = PACKAGES[case]
    files = {"pkg/__init__.py": "", **others, "pkg/sample.py": source}
    assert _forms(tmp_path, files, "pkg/sample.py", spelling) == expected


def test_every_form_is_known_by_an_object_a_checker_knows_it_as() -> None:
    assert set(FORM_ORIGINS.values()) == set(TYPING_FORM_NAMES)
    for origin, form in FORM_ORIGINS.items():
        assert origin.rpartition(".")[2] == form


def test_a_block_pins_only_what_its_module_binds_to_a_form(tmp_path: Path) -> None:
    source = (
        "from typing import cast as c\n"
        "from sqlglot import exp\n\n\n"
        "def run(raw):\n"
        "    value = c(Alpha, raw)\n"
        "    node = exp.cast(raw, 'INT')\n"
    )
    path = tmp_path / "sample.py"
    write_file(tmp_path / "pyproject.toml", PYPROJECT)
    write_file(path, source)
    function = ast.parse(source).body[2]
    assert isinstance(function, ast.FunctionDef)
    forms = typing_forms_of(function.body, ModuleText(str(path), source), ImportGraphCache())
    assert forms.denoted == frozenset({("c", "cast")})
    typed, own = function.body
    assert isinstance(typed, ast.Assign) and isinstance(typed.value, ast.Call)
    assert isinstance(own, ast.Assign) and isinstance(own.value, ast.Call)
    pins = statically_read(typed, DEFAULT_TRANSLATION_KEYWORDS, forms)
    assert pins.get(id(typed.value.args[0])) is Pin.WHOLE
    assert pins.get(id(typed.value.func)) is Pin.WHOLE
    assert not statically_read(own, DEFAULT_TRANSLATION_KEYWORDS, forms)
