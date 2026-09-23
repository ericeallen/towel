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

"""A method helper is class-private, so nothing outside its class can reach it.

docs/DECISIONS.md ("A method helper lives in the class that holds both
duplicates") names the helper ``__extracted_func_0``, which its class ``A``
stores as ``_A__extracted_func_0`` and its methods call as
``self.__extracted_func_0()``. A subclass, in the project or outside it, that
defines a member of the helper's old name, ``_extracted_func_0``, took its place
for every call ``A``'s methods made; its own ``__extracted_func_0`` is stored
under its own name. The name is chosen so the stored form collides with
nothing, and CPython's mangling rules are followed exactly: a class's leading
underscores are dropped, a class named only with underscores mangles nothing
and so gets no method helper, and a nested class mangles with its own name.

Every test runs the program before and after in a fresh interpreter and
compares what it printed.
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
from typing import Dict, List, Mapping

import pytest

from towel.unification.class_private import (
    is_class_private,
    mangled,
    mangling_classes,
    mangling_prefix,
)
from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine

# Two methods of one class sharing a block that reads the receiver, so the
# helper is a method of the class.
SHARED = """
    def first(self, items):
        print("first", len(items))
        total = self.base
        for item in items:
            total += item * 2
        return total

    def second(self, items):
        print("second", len(items))
        total = self.base
        for item in items:
            total += item * 2
        return total
"""

# A member of every name a generated helper could be given, old spelling and new.
EVERY_HELPER_NAME = """
for number in range(60):
    for prefix in ("_extracted_func", "__extracted_func"):
        setattr({cls}, f"{{prefix}}_{{number}}", lambda *args: "surprise")
"""


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


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


def _refactor(package: Path, **options: object) -> int:
    """Refactor ``package`` in place to a fixed point; the number of refactorings applied."""
    engine = UnificationRefactorEngine(min_lines=3, **options)  # type: ignore[arg-type]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), progress="none"
        )
    return sum(applied for applied, _ in results.values())


def _project(tmp_path: Path, files: Mapping[str, str]) -> Path:
    """A project root with packaging metadata holding ``files``; returns the root."""
    root = tmp_path / "project"
    _write(root, {"pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n', **files})
    return root


def _class_helpers(source: str) -> Dict[str, List[str]]:
    """The generated helpers in each class body of ``source``, nested classes included."""
    return {
        node.name: [
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef) and is_generated_helper_name(item.name)
        ]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
    }


def _module_helpers(source: str) -> List[str]:
    return [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    ]


# -- no subclass reaches the helper ---------------------------------------------


@pytest.mark.parametrize("where", ["outside-the-project", "in-the-project"])
def test_a_subclass_cannot_override_a_method_helper(tmp_path: Path, where: str) -> None:
    """A subclass with a member of every name a helper could get leaves ``A``'s alone.

    The members are made by ``setattr`` so that no scan of the project's
    sources can see them, and the subclass sits either outside the project's
    root, which no scan reads, or in a module of the project that is not
    being refactored. ``A().first`` must go on returning what it did.
    """
    library = "class A:\n    base = 1\n" + textwrap.indent(textwrap.dedent(SHARED), "    ")
    child = "from pkg.lib import A\n\n\nclass Child(A):\n    pass\n\n" + EVERY_HELPER_NAME.format(
        cls="Child"
    )
    files = {"pkg/__init__.py": "", "pkg/lib.py": library}
    if where == "in-the-project":
        files["app/child.py"] = child
    root = _project(tmp_path, files)
    path = [root]
    if where == "outside-the-project":
        _write(tmp_path / "elsewhere", {"app/child.py": child})
        path.append(tmp_path / "elsewhere")
    driver = (
        "from app.child import Child\nfrom pkg.lib import A\n"
        "print(Child().first([1, 2]), Child().second([3]), A().first([4]))\n"
    )
    before = _run(root, driver, *path)
    assert "surprise" not in before
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, *path) == before
    helpers = _class_helpers((root / "pkg" / "lib.py").read_text())["A"]
    assert len(helpers) == 1 and is_class_private(helpers[0]), helpers


def test_a_subclass_defining_the_old_helper_name_no_longer_renumbers_the_helper(
    tmp_path: Path,
) -> None:
    """A member named ``_extracted_func_0`` once had to be kept clear of; now it cannot collide.

    The subclass's own ``__extracted_func_0`` is stored as
    ``_Child__extracted_func_0``, so it claims nothing of ``A``'s either.
    """
    library = "class A:\n    base = 1\n" + textwrap.indent(textwrap.dedent(SHARED), "    ")
    child = """
        from pkg.lib import A


        class Child(A):
            def _extracted_func_0(self, *args):
                return "surprise"

            def __extracted_func_0(self, *args):
                return "surprise"

            def mine(self):
                return self.__extracted_func_0()
        """
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": library, "app/child.py": child})
    driver = (
        "from app.child import Child\nprint(Child().first([1, 2]), Child().second([3]),"
        " Child().mine())\n"
    )
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    assert _class_helpers((root / "pkg" / "lib.py").read_text())["A"] == ["__extracted_func_0"]


# -- same-class helpers keep working ---------------------------------------------

SAME_CLASS = """
class Base:
    def label(self, total):
        return f"base:{total}"

    @classmethod
    def unit(cls):
        return "u"


class Account(Base):
    def __init__(self):
        self.__balance = 10
        self._log = []

    def label(self, total):
        return "account:" + super().label(total)

    def deposit(self, amount):
        print("deposit", amount)
        self._log.append(("deposit", amount))
        self.__balance += amount
        total = self.__balance * 2
        return super().label(total)

    def withdraw(self, amount):
        print("withdraw", amount)
        self._log.append(("withdraw", amount))
        self.__balance += amount
        total = self.__balance * 2
        return super().label(-total)

    @classmethod
    def describe(cls, name):
        print("describe", name)
        parts = [cls.__name__, name, cls.unit()]
        joined = "/".join(parts)
        return joined.upper()

    @classmethod
    def summary(cls, name):
        print("summary", name)
        parts = [cls.__name__, name, cls.unit()]
        joined = "/".join(parts)
        return joined.lower()


class Savings(Account):
    @classmethod
    def unit(cls):
        return "s"
"""

SAME_CLASS_DRIVER = """
from pkg.lib import Account, Savings
for account in (Account(), Savings()):
    print(account.deposit(5), account.withdraw(-3), account.label(1), account._log)
    print(vars(account))
print(Account.describe("a"), Savings.summary("b"), Savings().describe("c"))
"""


def test_same_class_helpers_keep_private_attributes_super_and_classmethods_working(
    tmp_path: Path,
) -> None:
    """The helper reads ``self.__balance`` as ``Account`` stores it, beside zero-argument ``super()``.

    ``self.__balance`` in the helper is compiled in ``Account``'s body, so it is
    the same ``_Account__balance`` the methods read; the classmethods share a
    class-private classmethod reached through ``cls``, which ``Savings``
    inherits without being able to replace.
    """
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": SAME_CLASS})
    before = _run(root, SAME_CLASS_DRIVER, root)
    assert _refactor(root / "pkg") >= 2
    after = _run(root, SAME_CLASS_DRIVER, root)
    assert after == before
    source = (root / "pkg" / "lib.py").read_text()
    helpers = _class_helpers(source)
    assert len(helpers["Account"]) == 2 and all(map(is_class_private, helpers["Account"]))
    assert not helpers["Base"] and not helpers["Savings"] and not _module_helpers(source)
    assert "cls.__extracted_func_" in source and "self.__extracted_func_" in source


# -- mangling edge cases ---------------------------------------------------------


def test_a_class_with_leading_underscores_stores_the_helper_without_them(tmp_path: Path) -> None:
    library = "class __Hidden:\n    base = 1\n" + textwrap.indent(textwrap.dedent(SHARED), "    ")
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": library})
    driver = (
        "from pkg import lib\nbox = getattr(lib, '__Hidden')()\n"
        "print(box.first([1]), box.second([2]),"
        " sorted(n for n in vars(type(box)) if 'extracted' in n))\n"
    )
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    after = _run(root, driver, root)
    assert after.split("[")[0] == before.split("[")[0]
    helpers = _class_helpers((root / "pkg" / "lib.py").read_text())["__Hidden"]
    assert helpers == ["__extracted_func_0"]
    assert "'_Hidden__extracted_func_0'" in after


@pytest.mark.parametrize("name", ["_", "__", "___"])
def test_a_class_named_only_with_underscores_gets_a_module_helper(
    tmp_path: Path, name: str
) -> None:
    """``class __`` mangles nothing, so a ``__extracted_func_0`` there is an ordinary member."""
    library = f"class {name}:\n    base = 1\n" + textwrap.indent(textwrap.dedent(SHARED), "    ")
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": library})
    driver = f"from pkg import lib\nbox = getattr(lib, {name!r})()\nprint(box.first([1]), box.second([2]))\n"
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    source = (root / "pkg" / "lib.py").read_text()
    assert not _class_helpers(source)[name] and len(_module_helpers(source)) == 1


def test_a_nested_class_keeps_its_own_private_names(tmp_path: Path) -> None:
    """A class nested in the host mangles with its own name, so its ``__extracted_func_0`` is its own."""
    library = (
        "class A:\n    base = 1\n\n"
        "    class Inner:\n"
        "        def __extracted_func_0(self):\n            return 'inner'\n\n"
        "        def call(self):\n            return self.__extracted_func_0()\n"
        + textwrap.indent(textwrap.dedent(SHARED), "    ")
    )
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": library})
    driver = "from pkg.lib import A\nprint(A().first([1]), A().second([2]), A.Inner().call())\n"
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    helpers = _class_helpers((root / "pkg" / "lib.py").read_text())
    assert helpers["Inner"] == ["__extracted_func_0"] and len(helpers["A"]) == 1
    assert helpers["A"] != ["__extracted_func_0"]


def test_the_stored_name_collides_with_nothing_the_class_already_has(tmp_path: Path) -> None:
    """``_A__extracted_func_0`` written out is what ``__extracted_func_0`` in ``A`` would be."""
    library = (
        "class A:\n    base = 1\n\n"
        "    def _A__extracted_func_0(self, *args):\n        return 'mine'\n"
        + textwrap.indent(textwrap.dedent(SHARED), "    ")
    )
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": library})
    driver = "from pkg.lib import A\nprint(A().first([1]), A().second([2]), A()._A__extracted_func_0())\n"
    before = _run(root, driver, root)
    assert _refactor(root / "pkg") > 0
    assert _run(root, driver, root) == before
    helpers = _class_helpers((root / "pkg" / "lib.py").read_text())["A"]
    assert len(helpers) == 1 and helpers != ["__extracted_func_0"]


def test_a_second_run_numbers_past_the_first_runs_helpers(tmp_path: Path) -> None:
    """Earlier Towel output in the class is a member like any other, and the next helper avoids it."""
    library = (
        "class A:\n    base = 1\n"
        + textwrap.indent(textwrap.dedent(SHARED), "    ")
        + textwrap.indent(
            textwrap.dedent(SHARED)
            .replace("def first", "def third")
            .replace("def second", "def fourth")
            .replace("item * 2", "item * 3"),
            "    ",
        )
    )
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": library})
    driver = (
        "from pkg.lib import A\n"
        "print(A().first([1]), A().second([2]), A().third([3]), A().fourth([4]))\n"
    )
    before = _run(root, driver, root)
    # One helper per run: the first run is capped at a single refactoring.
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none", max_iterations=1
        )
    first = _class_helpers((root / "pkg" / "lib.py").read_text())["A"]
    assert _refactor(root / "pkg") > 0
    helpers = _class_helpers((root / "pkg" / "lib.py").read_text())["A"]
    assert len(first) == 1 and set(first) < set(helpers), helpers
    assert len(set(helpers)) == len(helpers), helpers
    assert _run(root, driver, root) == before


# -- the mangling rule, as CPython applies it ------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("__x", True),
        ("__extracted_func_0", True),
        ("_x", False),
        ("x", False),
        ("__x__", False),
        ("__", False),
        ("___", False),
        ("__a.b", False),
    ],
)
def test_which_names_are_class_private(name: str, expected: bool) -> None:
    assert is_class_private(name) is expected


@pytest.mark.parametrize(
    "class_name, name",
    [
        ("A", "__x"),
        ("_A", "__x"),
        ("__A__", "__x"),
        ("_", "__x"),
        ("__", "__x"),
        ("A", "__x__"),
        ("A", "_x"),
        ("A_B", "__x_y"),
    ],
)
def test_mangling_agrees_with_the_compiler(class_name: str, name: str) -> None:
    source = f"class {class_name}:\n    {name} = 1\n"
    namespace: Dict[str, object] = {}
    exec(compile(source, "<m>", "exec"), namespace)
    stored = [key for key in vars(namespace[class_name]) if key.endswith(name.lstrip("_"))]
    assert stored == [mangled(name, class_name)]
    assert mangling_prefix(class_name) == (
        None if not class_name.lstrip("_") else "_" + class_name.lstrip("_")
    )


def test_the_innermost_class_body_decides_and_a_header_belongs_outside() -> None:
    source = textwrap.dedent("""
        class Outer:
            def method(self):
                return self.__a

            class Inner(Base):
                value = __b

                def method(self):
                    return lambda: self.__c
        """)
    tree = ast.parse(source)
    owners = mangling_classes(tree)
    spelled = {
        node.attr if isinstance(node, ast.Attribute) else node.id: owners[node]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
        and "__" in getattr(node, "attr", "") + getattr(node, "id", "")
    }
    assert {name: owner.name if owner else None for name, owner in spelled.items()} == {
        "__a": "Outer",
        "__b": "Inner",
        "__c": "Inner",
    }
    base = next(node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Base")
    owner = owners[base]
    assert owner is not None and owner.name == "Outer"
    # What the compiler made of the same source.
    code = compile(source.replace("(Base)", ""), "<m>", "exec")
    names: set[str] = set()
    pending = [code]
    while pending:
        unit = pending.pop()
        names.update(unit.co_names)
        pending.extend(const for const in unit.co_consts if hasattr(const, "co_names"))
    assert {"_Outer__a", "_Inner__b", "_Inner__c"} <= names


# -- the audit's reproducer --------------------------------------------------------

requires_checkers = pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None or importlib.util.find_spec("pyright") is None,
    reason="mypy or pyright absent",
)

TYPED = """
from __future__ import annotations


class Ledger:
    def __init__(self) -> None:
        self.__entries: list[int] = []
        self._name = "ledger"

    def credit(self, amount: int) -> str:
        print("credit", amount)
        self.__entries.append(amount)
        total = sum(self.__entries)
        return f"{self._name}:{total}"

    def refund(self, amount: int) -> str:
        print("refund", amount)
        self.__entries.append(amount)
        total = sum(self.__entries)
        return f"{self._name}:{total}"


class Audited(Ledger):
    def __extracted_func_0(self, text: str) -> str:
        return text.upper()

    def shout(self) -> str:
        return self.__extracted_func_0("x")
"""


def _strict_errors(tool: str, path: Path) -> List[str]:
    """The errors ``tool`` in strict mode reports for the module at ``path``; asserts it ran."""
    if tool == "mypy":
        command = [sys.executable, "-m", "mypy", "--strict", "--no-incremental", path.name]
        marker = ": error:"
    else:
        (path.parent / "pyrightconfig.json").write_text('{"typeCheckingMode": "strict"}\n')
        command = [sys.executable, "-m", "pyright", path.name]
        marker = " - error:"
    completed = subprocess.run(
        command, cwd=path.parent, capture_output=True, text=True, timeout=300, check=False
    )
    errors = [line for line in completed.stdout.splitlines() if marker in line]
    assert completed.returncode == (1 if errors else 0), completed.stdout + completed.stderr
    return errors


@requires_checkers
def test_a_class_private_helper_passes_mypy_strict_and_pyright_strict(tmp_path: Path) -> None:
    """The helper Towel writes, beside a subclass with its own ``__extracted_func_0``, checks.

    The subclass's helper takes and returns ``str`` where ``Ledger``'s returns
    ``int``: had the two been one attribute, both checkers would reject the
    override. Towel's own run is checked by the project's checker as well.
    """
    from towel.type_inference import MypyInferrer

    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    (tmp_path / "ledger.py").write_text(TYPED.lstrip())
    for tool in ("mypy", "pyright"):
        assert _strict_errors(tool, tmp_path / "ledger.py") == []
    driver = "from ledger import Audited\nledger = Audited()\nprint(ledger.credit(3), ledger.refund(1), ledger.shout())\n"
    before = _run(tmp_path, driver, tmp_path)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _, applied, _ = engine.refactor_to_fixed_point(str(tmp_path / "ledger.py"))
    finally:
        oracle.close()
    assert applied == 1
    source = (tmp_path / "ledger.py").read_text()
    helpers = _class_helpers(source)
    assert len(helpers["Ledger"]) == 1 and is_class_private(helpers["Ledger"][0])
    helper = next(
        item
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "Ledger"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and is_generated_helper_name(item.name)
    )
    assert helper.returns is not None and all(arg.annotation for arg in helper.args.args[1:])
    assert "self.__entries" in ast.unparse(helper), source
    assert _run(tmp_path, driver, tmp_path) == before
    for tool in ("mypy", "pyright"):
        assert _strict_errors(tool, tmp_path / "ledger.py") == [], (tool, source)
