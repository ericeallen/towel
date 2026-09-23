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

"""A helper goes into a class only when both duplicates are methods of that class.

docs/DECISIONS.md ("A method helper lives in the class that holds both
duplicates") rules out the placement Towel used to make: a block shared by
two subclasses was hoisted into a base they had in common, a class that held
none of the code. Every subclass of that base inherited the helper, including
subclasses outside the project that Towel could not see, and one of them that
already had a member of the helper's name took its place for every call the
base's methods made. Blocks shared across classes now become a module-level
function that takes the receiver, and no class gains a member unless the code
it replaces was already in that class.

Each test runs the program before and after, in a fresh interpreter, and
compares what it printed; inspecting the generated source only confirms where
the helper went.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, List, Mapping, Tuple

import pytest

from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine

# Three statements, the default minimum, shared by the two classes' methods;
# each reads its receiver, so the helper would dispatch if it were a method.
VALIDATE = """
    def validate(self, value):
        if not value or not self.ready:
            raise ValueError("required")
        if len(value) < 3:
            raise ValueError("too short")
        if value.startswith("!"):
            raise ValueError("bang not allowed")
        return value.upper() + self.suffix
"""

SIBLINGS = (
    "class Base:\n    ready = True\n\n\n"
    "class Email(Base):\n    suffix = '@'\n"
    + textwrap.indent(textwrap.dedent(VALIDATE), "    ")
    + "\n\nclass Sms(Base):\n    suffix = '#'\n"
    + textwrap.indent(textwrap.dedent(VALIDATE), "    ")
)

PARENT_AND_CHILD = (
    "class Base:\n    ready = True\n    suffix = '@'\n"
    + textwrap.indent(textwrap.dedent(VALIDATE), "    ")
    + "\n\nclass Child(Base):\n    suffix = '#'\n\n"
    + textwrap.indent(textwrap.dedent(VALIDATE).replace("def validate", "def check"), "    ")
)

# Every call a helper serves, with a failing input for each branch.
PROBES = """
def probe(receiver, method):
    for value in ("valid", "", "x", "!bad"):
        try:
            print(type(receiver).__name__, method, getattr(receiver, method)(value))
        except ValueError as error:
            print(type(receiver).__name__, method, "ValueError", error)
"""


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _run(directory: Path, driver: str, *path: Path) -> str:
    """What ``driver`` prints, run in a fresh interpreter with ``path`` importable."""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(driver)],
        cwd=directory,
        capture_output=True,
        text=True,
        env={
            "PATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": ":".join(str(entry) for entry in path),
        },
        timeout=60,
        check=False,
    )
    return completed.stdout + "|" + "".join(completed.stderr.strip().splitlines()[-1:])


def _refactor(package: Path) -> int:
    """Refactor ``package`` in place to a fixed point, as ``towel dry PKG PKG --no-types`` does."""
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), progress="none"
        )
    return sum(applied for applied, _ in results.values())


def _helpers(source: str) -> Tuple[List[str], Dict[str, List[str]]]:
    """The generated helpers ``source`` defines at module level, and in each class's body."""
    tree = ast.parse(source)
    module = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    ]
    classes = {
        node.name: [
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef) and is_generated_helper_name(item.name)
        ]
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    return module, classes


def _project(tmp_path: Path, library: str) -> Path:
    """A project root holding ``pkg/lib.py``; returns the root."""
    root = tmp_path / "project"
    _write(
        root,
        {
            "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
            "pkg/__init__.py": "",
            "pkg/lib.py": library + PROBES,
        },
    )
    return root


def test_sibling_classes_share_a_module_helper_that_takes_the_receiver(tmp_path: Path) -> None:
    root = _project(tmp_path, SIBLINGS)
    driver = (
        "from pkg.lib import Email, Sms, probe\n"
        "probe(Email(), 'validate')\nprobe(Sms(), 'validate')\n"
    )
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    after = _run(root, driver, root)
    assert after == before
    module, classes = _helpers((root / "pkg" / "lib.py").read_text())
    assert len(module) == 1 and not any(classes.values()), classes
    source = (root / "pkg" / "lib.py").read_text()
    assert source.count(f"return {module[0]}(self, value)") == 2, source


def test_a_parent_and_its_child_share_a_module_helper(tmp_path: Path) -> None:
    root = _project(tmp_path, PARENT_AND_CHILD)
    driver = (
        "from pkg.lib import Base, Child, probe\n"
        "probe(Base(), 'validate')\nprobe(Child(), 'check')\nprobe(Child(), 'validate')\n"
    )
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    module, classes = _helpers((root / "pkg" / "lib.py").read_text())
    assert len(module) == 1 and not any(classes.values()), classes


def test_a_subclass_outside_the_project_cannot_take_a_shared_helpers_place(
    tmp_path: Path,
) -> None:
    """A subclass Towel cannot see, holding members of every name a helper could get.

    Hoisted into ``Base``, the helper shared by ``Email`` and ``Sms`` was a
    member of every subclass, and ``Outside`` overrode it: its instances
    returned ``'surprise'`` from ``validate``. The project scan that reserves
    helper names never reads a module outside the project's root, and the
    members here are made by ``setattr``, which no scan reads either.
    """
    root = _project(tmp_path, SIBLINGS)
    _write(
        tmp_path / "elsewhere",
        {
            "outside.py": textwrap.dedent("""
                from pkg.lib import Email


                class Outside(Email):
                    pass


                for number in range(50):
                    for prefix in ("_extracted_func", "__extracted_func"):
                        setattr(Outside, f"{prefix}_{number}", lambda *args: "surprise")
                """),
        },
    )
    driver = (
        "from outside import Outside\nfrom pkg.lib import probe\nprobe(Outside(), 'validate')\n"
    )
    path = (root, tmp_path / "elsewhere")
    before = _run(root, driver, *path)
    assert "surprise" not in before
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, *path) == before


REBINDINGS = {
    "assignment": "class Base:\n    pass\n\n\n{alpha}Base = object\n\n\n{beta}",
    "branch": "REPLACE = True\n\n\nclass Base:\n    pass\n\n\n{alpha}if REPLACE:\n    Base = object\n\n\n{beta}",
}


def _subclass(name: str, tail: int) -> str:
    return (
        f"class {name}(Base):\n    offset = 0\n\n    def compute(self, value):\n"
        "        first = value + 1\n        second = first * 2\n        third = second + 5\n"
        f"        return third + {tail} + self.offset\n\n\n"
    )


@pytest.mark.parametrize("rebinding", sorted(REBINDINGS))
def test_programs_that_rebind_a_base_between_its_subclasses_keep_their_output(
    tmp_path: Path, rebinding: str
) -> None:
    """``Base`` denotes one class where ``Alpha`` is defined and ``object`` where ``Beta`` is.

    These broke hoisting, which put the helper in the class statement named
    ``Base`` and left ``Beta`` without it; they stay as programs whose output
    the module-level helper must keep.
    """
    library = REBINDINGS[rebinding].format(alpha=_subclass("Alpha", 1), beta=_subclass("Beta", 2))
    root = _project(tmp_path, library)
    driver = "from pkg.lib import Alpha, Beta\nprint(Alpha().compute(3), Beta().compute(4))\n"
    before = _run(root, driver, root)
    assert before == "14 17\n|"
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    module, classes = _helpers((root / "pkg" / "lib.py").read_text())
    assert len(module) == 1 and not any(classes.values()), classes


def test_the_class_holding_both_duplicates_still_takes_the_helper(tmp_path: Path) -> None:
    """The positive control: a rule that refused every class would pass the tests above."""
    library = (
        "class Box:\n    ready = True\n    suffix = '!'\n"
        + textwrap.indent(textwrap.dedent(VALIDATE), "    ")
        + textwrap.indent(textwrap.dedent(VALIDATE).replace("def validate", "def check"), "    ")
    )
    root = _project(tmp_path, library)
    driver = "from pkg.lib import Box, probe\nprobe(Box(), 'validate')\nprobe(Box(), 'check')\n"
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    module, classes = _helpers((root / "pkg" / "lib.py").read_text())
    assert not module and len(classes["Box"]) == 1, classes


SHAPES = """
from __future__ import annotations


class Shape:
    name: str = "shape"


class Square(Shape):
    def __init__(self, side: int) -> None:
        self.side = side

    def describe(self, unit: str) -> str:
        label = self.name.upper()
        size = f"{self.side}{unit}"
        text = f"{label}: {size}"
        return text


class Circle(Shape):
    def __init__(self, radius: int) -> None:
        self.side = radius * 2

    def summary(self, unit: str) -> str:
        label = self.name.upper()
        size = f"{self.side}{unit}"
        text = f"{label}: {size}"
        return text
"""


@pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None or importlib.util.find_spec("pyright") is None,
    reason="mypy or pyright absent",
)
def test_the_module_function_siblings_share_passes_mypy_strict_and_pyright_strict(
    tmp_path: Path,
) -> None:
    """The receiver of the module function is typed as the classes whose methods call it.

    It is written ``self: Square | Circle`` and placed after both classes, so
    the annotation names them bare; the project's mypy and pyright, both
    strict, accept the module Towel writes.
    """
    from tests.test_class_private_helpers import _strict_errors
    from towel.type_inference import MypyInferrer

    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    path = tmp_path / "shapes.py"
    path.write_text(SHAPES.lstrip())
    for tool in ("mypy", "pyright"):
        assert _strict_errors(tool, path) == []
    driver = (
        "from shapes import Circle, Square\n"
        "print(Square(3).describe('cm'), Circle(2).summary('in'))\n"
    )
    before = _run(tmp_path, driver, tmp_path)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _, applied, _ = engine.refactor_to_fixed_point(str(path))
    finally:
        oracle.close()
    assert applied == 1
    source = path.read_text()
    module, classes = _helpers(source)
    assert len(module) == 1 and not any(classes.values()), source
    helper = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
    receiver = helper.args.args[0]
    assert receiver.arg == "self" and receiver.annotation is not None
    assert ast.unparse(receiver.annotation) == "Square | Circle", source
    assert _run(tmp_path, driver, tmp_path) == before
    for tool in ("mypy", "pyright"):
        assert _strict_errors(tool, path) == [], (tool, source)
