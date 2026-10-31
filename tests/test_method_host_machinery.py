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

"""Private method helpers respect receiver contracts without policing reflective hooks.

No-op metaclasses, subclass hooks and attribute hooks must not block extraction.
Namespace inspection and extra attribute-lookup events are outside equivalence;
explicit decorators, declared receiver types and private naming still apply.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, Tuple

import pytest

from tests.test_helpers import method_helper_calls, refactor_to_fixed_point_silently
from towel.unification.refactor_engine import UnificationRefactorEngine

METHODS = """
    def first(self, n):
        print("first", n)
        total = self.v + n
        total = total * 2
        print("done", total)
        return total

    def second(self, n):
        print("second", n)
        total = self.v + n
        total = total * 2
        print("done", total)
        return total
"""

DRIVER = "from box import Box\nprint(Box().first(1), Box().second(2))\n"


def _module(prelude: str, header: str, methods: str = METHODS) -> str:
    """``prelude``, then the class ``header`` opens with ``methods`` in its body."""
    body = textwrap.indent(textwrap.dedent(methods).strip("\n"), "    ")
    return textwrap.dedent(prelude).strip("\n") + "\n\n\n" + header + "\n" + body + "\n"


def _run(directory: Path, driver: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(driver)],
        cwd=directory,
        capture_output=True,
        text=True,
        env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout + "|" + "".join(completed.stderr.strip().splitlines()[-1:])


def _refactored(
    tmp_path: Path, source: str, driver: str = DRIVER, *, moves: bool = True
) -> Tuple[str, str, str]:
    """The driver's output before and after refactoring ``box.py`` to a fixed point, and the result.

    ``moves`` says whether the run is to move code at all.
    """
    path = tmp_path / "box.py"
    path.write_text(source)
    before = _run(tmp_path, driver)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert (applied > 0) == moves
    assert moves or final == source
    path.write_text(final)
    return before, _run(tmp_path, driver), final


def _refactored_project(
    tmp_path: Path, files: Dict[str, str], driver: str, *, moves: bool = True
) -> Tuple[str, str, str]:
    """As :func:`_refactored`, for a directory of modules; the result is ``box.py``'s."""
    for name, text in files.items():
        (tmp_path / name).write_text(textwrap.dedent(text).lstrip("\n"))
    before = _run(tmp_path, driver)
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
    assert (sum(applied for applied, _ in results.values()) > 0) == moves
    return before, _run(tmp_path, driver), (tmp_path / "box.py").read_text()


def test_a_receiver_annotated_as_a_protocol_takes_a_module_helper(tmp_path: Path) -> None:
    source = _module(
        """
        from typing import Protocol


        class HasV(Protocol):
            v: int
        """,
        "class Box:\n    v = 1\n",
        METHODS.replace("(self, n)", "(self: HasV, n)"),
    )
    driver = """
        from types import SimpleNamespace
        from box import Box
        print(Box().first(1), Box.second(SimpleNamespace(v=10), 1))
    """
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert not method_helper_calls(final)


def test_a_class_method_whose_receiver_may_be_another_class_takes_a_module_helper(
    tmp_path: Path,
) -> None:
    methods = METHODS.replace("    def ", "    @classmethod\n    def ").replace("self", "cls")
    source = _module("", "class Box:\n    v = 1\n", methods.replace("(cls, n)", "(cls: type, n)"))
    driver = """
        from box import Box


        class Other:
            v = 10


        print(Box.first(1), vars(Box)["second"].__func__(Other, 1))
    """
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert not method_helper_calls(final)


@pytest.mark.parametrize(
    "annotation, prelude",
    [
        ('"Box"', ""),
        ("Box", "from __future__ import annotations"),
        ("Self", "from typing import Self"),
        ("typing.Self", "import typing"),
        ("T", 'from typing import TypeVar\n\nT = TypeVar("T", bound="Box")'),
    ],
    ids=["own-class", "own-class-postponed", "self-type", "self-type-attribute", "bound-variable"],
)
def test_a_receiver_annotated_as_its_own_class_keeps_the_method_helper(
    tmp_path: Path, annotation: str, prelude: str
) -> None:
    source = _module(
        prelude, "class Box:\n    v = 1\n", METHODS.replace("(self, n)", f"(self: {annotation}, n)")
    )
    before, after, final = _refactored(tmp_path, source)
    assert after == before
    assert method_helper_calls(final)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 input requires Python 3.12")
def test_a_receiver_of_a_type_parameter_bound_to_its_class_keeps_the_method_helper(
    tmp_path: Path,
) -> None:
    source = _module(
        "", "class Box:\n    v = 1\n", METHODS.replace("(self, n)", "[T: Box](self: T, n)")
    )
    before, after, final = _refactored(tmp_path, source)
    assert after == before
    assert method_helper_calls(final)


def test_a_receiver_of_a_variable_bound_elsewhere_takes_a_module_helper(tmp_path: Path) -> None:
    source = _module(
        """
        from typing import TypeVar


        class Base:
            v = 10


        T = TypeVar("T", bound=Base)
        """,
        "class Box(Base):\n    v = 1\n",
        METHODS.replace("(self, n)", "(self: T, n)"),
    )
    driver = "from box import Base, Box\nprint(Box().first(1), Box.second(Base(), 1))\n"
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert not method_helper_calls(final)


PLAIN_METACLASS = """
class Meta(type):
    def __new__(mcs, name, bases, namespace):
        return super().__new__(mcs, name, bases, namespace)
"""


@pytest.mark.parametrize(
    "prelude, header, driver",
    [
        (PLAIN_METACLASS, "class Box(metaclass=Meta):\n    v = 1\n", DRIVER),
        (
            "class Base:\n    def __init_subclass__(cls, **kwargs):\n"
            "        super().__init_subclass__(**kwargs)\n        print('created', cls.__name__)\n",
            "class Box(Base):\n    v = 1\n",
            DRIVER,
        ),
        (
            "",
            "class Box:\n    v = 1\n\n    def __init_subclass__(cls, **kwargs):\n"
            "        super().__init_subclass__(**kwargs)\n        print('created', cls.__name__)\n",
            "from box import Box\nclass Sub(Box):\n    pass\nprint(Sub().first(1), Sub().second(2))\n",
        ),
        (
            "class Base:\n    def __init_subclass__(cls, label, **kwargs):\n"
            "        super().__init_subclass__(**kwargs)\n        cls.label = label\n",
            "class Box(Base, label='box'):\n    v = 1\n",
            DRIVER + "print(Box.label)\n",
        ),
    ],
    ids=["metaclass", "inherited-init-subclass", "own-init-subclass", "class-keyword"],
)
def test_implicit_class_hooks_allow_private_method_helpers(
    tmp_path: Path, prelude: str, header: str, driver: str
) -> None:
    before, after, final = _refactored(tmp_path, _module(prelude, header), driver)
    assert after == before
    assert method_helper_calls(final)


@pytest.mark.parametrize("inherited", [False, True])
def test_attribute_lookup_hooks_allow_private_method_helpers(
    tmp_path: Path, inherited: bool
) -> None:
    lookup = (
        "    def __getattribute__(self, name):\n"
        "        return object.__getattribute__(self, name)\n"
    )
    prelude = "class Base:\n" + lookup if inherited else ""
    header = "class Box(Base):\n    v = 1\n" if inherited else "class Box:\n    v = 1\n" + lookup
    before, after, final = _refactored(tmp_path, _module(prelude, header))
    assert after == before
    assert method_helper_calls(final)


def test_logging_attribute_lookup_does_not_block_private_helpers(tmp_path: Path) -> None:
    source = _module(
        "LOOKUPS = []",
        "class Box:\n    v = 1\n\n    def __getattribute__(self, name):\n"
        "        LOOKUPS.append(name)\n        return object.__getattribute__(self, name)\n",
    )
    before, after, final = _refactored(tmp_path, source)
    assert after == before
    assert method_helper_calls(final)
    # The ordinary computation is preserved. Reflecting on the additional
    # lookup sees the helper, as the documented boundary permits.
    observed = _run(
        tmp_path,
        "from box import Box, LOOKUPS\nBox().first(1)\n"
        "print(any(name.startswith('_Box__extracted_func_') for name in LOOKUPS))\n",
    )
    assert observed.endswith("True\n|")


def test_a_class_whose_getattr_serves_missing_names_keeps_the_method_helper(tmp_path: Path) -> None:
    source = _module(
        "",
        "class Box:\n    v = 1\n\n    def __getattr__(self, name):\n        return 'field ' + name\n",
    )
    driver = (
        "from box import Box\nbox = Box()\nprint(box.first(1), box.second(2),"
        " box._extracted_func_0, box.__extracted_func_0)\n"
    )
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert method_helper_calls(final)


def test_a_metaclass_hosts_private_method_helpers(tmp_path: Path) -> None:
    """A metaclass's instances are classes, and ``type`` looks their attributes up its own way."""
    source = _module("", "class Meta(type):\n    v = 1\n")
    driver = (
        "from box import Meta\nKind = Meta('Kind', (), {})\nprint(Kind.first(1), Kind.second(2))\n"
    )
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert method_helper_calls(final)


def test_a_super_subclass_hosts_private_method_helpers(tmp_path: Path) -> None:
    """Delegation falls back to the receiver's class for its private helper."""
    source = _module(
        "class Target:\n    v = 1\n\nclass Child(Target):\n    pass\n",
        "class Box(super):",
    )
    driver = (
        "from box import Box, Child\nbox = Box(Child, Child())\n"
        "print(box.first(1), box.second(2))\n"
    )
    before, after, final = _refactored(tmp_path, source, driver)
    assert before.endswith("4 6\n|")
    assert after == before
    assert method_helper_calls(final)


def test_an_imported_base_with_a_metaclass_allows_private_method_helpers(tmp_path: Path) -> None:
    before, after, final = _refactored_project(
        tmp_path,
        {
            "meta.py": PLAIN_METACLASS + "\n\nclass Base(metaclass=Meta):\n    pass\n",
            "box.py": _module("from meta import Base", "class Box(Base):\n    v = 1\n"),
        },
        DRIVER,
    )
    assert after == before
    assert method_helper_calls(final)


def test_a_base_imported_from_the_project_is_judged_where_it_is_defined(tmp_path: Path) -> None:
    before, after, final = _refactored_project(
        tmp_path,
        {
            "base.py": "import abc\n\n\nclass Base(abc.ABC):\n    pass\n",
            "box.py": _module("from base import Base", "class Box(Base):\n    v = 1\n"),
        },
        DRIVER,
    )
    assert after == before
    assert method_helper_calls(final)


ENUM_BOX = "class Box(enum.Enum):\n    ONE = 1\n\n    @property\n    def v(self):\n        return self.value\n"


@pytest.mark.parametrize(
    "prelude, header",
    [
        ("import abc", "class Box(abc.ABC):\n    v = 1\n"),
        ("from abc import ABCMeta as Meta", "class Box(metaclass=Meta):\n    v = 1\n"),
        (
            'from typing import Generic, TypeVar\n\nT = TypeVar("T")',
            "class Box(Generic[T]):\n    v = 1\n",
        ),
        ("class Base:\n    pass", "class Box(Base):\n    v = 1\n"),
        ("", "class Box(dict):\n    v = 1\n"),
        ("import enum", ENUM_BOX),
        ("import enum", ENUM_BOX.replace("enum.Enum", "enum.IntEnum")),
    ],
    ids=["abc", "abcmeta", "generic", "plain-base", "builtin-base", "enum", "int-enum"],
)
def test_standard_class_forms_allow_private_method_helpers(
    tmp_path: Path, prelude: str, header: str
) -> None:
    driver = (
        "from box import Box\nprint(Box.ONE.first(1), Box.ONE.second(2), list(Box))\n"
        if "enum" in prelude
        else DRIVER
    )
    before, after, final = _refactored(tmp_path, _module(prelude, header), driver)
    assert after == before
    assert method_helper_calls(final)
