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

"""Every name Towel binds in a module for a helper's annotations is private, and rebinds nothing.

A module-level binding is seen by the module's later code, by every consumer
as an attribute, and, where the module has no ``__all__``, by every module
that star-imports it. So the materializer's import writers are called here
directly, one kind of import at a time, on small modules that already bind
the name the annotation wants, and each test asserts the name the writer
bound, that it is private, and that no binding the module had changed:
``_bindings`` lists, for every name the module's own scope binds, the
statements that bind it. The end-to-end tests run the audit's reproductions
under mypy and compare what each program does before and after.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, Iterable, List, Set, Tuple
import types

import pytest

from towel.type_inference import MypyInferrer
from towel.unification.annotation_imports import words_of
from towel.unification.exceptions import RefactoringError
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.statement_facts import bindings_of

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

HELPER = "__extracted_func_0"


def _bindings(source: str) -> Dict[str, List[str]]:
    """Each name the module's own scope binds, with the statements binding it, in order.

    A guarded import counts: a checker reads it as a binding like any other.
    """
    found: Dict[str, List[str]] = {}
    for statement in ast.parse(source).body:
        for name in sorted(bindings_of(statement, into_nested_scopes=False)):
            found.setdefault(name, []).append(ast.unparse(statement))
    return found


def _new_names(before: str, after: str) -> Set[str]:
    """The names ``after`` binds that ``before`` did not; fails if any binding of ``before`` changed."""
    old, new = _bindings(before), _bindings(after)
    for name, statements in old.items():
        assert new.get(name) == statements, f"{name} is rebound:\n{after}"
    return set(new) - set(old)


def _assert_private_and_free(names: Iterable[str], before: str) -> None:
    for name in names:
        assert name.startswith("_"), f"{name} is public"
        assert name == HELPER or name not in words_of(before), f"{name} was already spelled"


def _proposal(
    path: Path,
    signature: str,
    *,
    required: Tuple[Tuple[str, str], ...] = (),
    checking: Tuple[Tuple[str, str], ...] = (),
    declarations: str = "",
) -> RefactoringProposal:
    helper = ast.parse(f"def {HELPER}({signature}):\n    return None\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    return RefactoringProposal(
        file_path=str(path),
        extracted_function=helper,
        replacements=[],
        description="",
        parameters_count=len(helper.args.args),
        required_imports=required,
        type_checking_imports=checking,
        helper_type_declarations=tuple(ast.parse(textwrap.dedent(declarations)).body),
    )


def _inserted(tmp_path: Path, source: str, signature: str, **wanted: object) -> str:
    """``source`` with the helper inserted by the materializer, its imports written first."""
    path = tmp_path / "m.py"
    source = textwrap.dedent(source)
    path.write_text(source, encoding="utf-8")
    lines = source.splitlines(keepends=True)
    UnificationRefactorEngine()._insert_helper(
        _proposal(path, signature, **wanted), str(path), lines  # type: ignore[arg-type]
    )
    return "".join(lines)


def _described(value: object) -> str:
    """A module, class or function by its name; anything else by its type and value."""
    if isinstance(value, types.ModuleType):
        return f"module {value.__name__}"
    if isinstance(value, type) or callable(value) and hasattr(value, "__qualname__"):
        kind = "class" if isinstance(value, type) else "function"
        return f"{kind} {getattr(value, '__module__', None)}.{getattr(value, '__qualname__')}"
    return f"{type(value).__qualname__} {value!r}"


def _public(source: str) -> Dict[str, str]:
    """What running ``source`` leaves in its namespace under public names, described."""
    namespace: Dict[str, object] = {"__name__": "module_under_test"}
    exec(compile(source, "<module>", "exec"), namespace)
    return {
        name: _described(value) for name, value in namespace.items() if not name.startswith("_")
    }


# -- typing names the annotations use -------------------------------------------------


def test_a_typing_name_is_reached_through_a_private_alias_of_typing(tmp_path: Path) -> None:
    before = "import os\n\n\ndef f(x: int) -> int:\n    return x\n"
    after = _inserted(
        tmp_path,
        before,
        "a: Any, b: Callable[[], int]",
        required=(
            ("typing", "Any"),
            ("typing", "Callable"),
        ),
    )
    assert _new_names(before, after) == {HELPER, "_typing"}
    _assert_private_and_free([HELPER, "_typing"], before)
    assert "import typing as _typing\n" in after
    assert f"def {HELPER}(a: _typing.Any, b: _typing.Callable[[], int]):" in after, after
    assert _public(after) == _public(before)


def test_a_typing_name_the_module_binds_otherwise_is_not_rebound(tmp_path: Path) -> None:
    """The try/except import is no import statement, and the star import names nothing."""
    before = textwrap.dedent("""
        from collections.abc import *
        try:
            from not_installed import Any
        except ImportError:
            class Any:
                pass
        import os
        """)
    after = _inserted(
        tmp_path,
        before,
        "a: Any, b: Callable[[], int]",
        required=(
            ("typing", "Any"),
            ("typing", "Callable"),
        ),
    )
    assert _new_names(before, after) == {HELPER, "_typing"}
    public = _public(after)
    assert public == _public(before)
    assert public["Any"] == "class module_under_test.Any"
    assert public["Callable"] == "class collections.abc.Callable"


def test_a_typing_name_bound_only_in_a_function_still_gets_a_module_binding(
    tmp_path: Path,
) -> None:
    """The line was in the file, so no import was written, and the annotation raised NameError."""
    before = textwrap.dedent("""
        def loader() -> None:
            from typing import Callable
            print(Callable)
        """)
    after = _inserted(tmp_path, before, "b: Callable[[], int]", required=(("typing", "Callable"),))
    assert _new_names(before, after) == {HELPER, "_typing"}
    assert _public(after).keys() == _public(before).keys()


def test_an_existing_binding_of_typing_is_used_rather_than_a_new_one(tmp_path: Path) -> None:
    before = "import typing\n\n\ndef f() -> None:\n    return None\n"
    after = _inserted(tmp_path, before, "a: Any", required=(("typing", "Any"),))
    assert _new_names(before, after) == {HELPER}
    assert f"def {HELPER}(a: typing.Any):" in after, after


def test_the_alias_an_earlier_refactoring_wrote_is_reused(tmp_path: Path) -> None:
    before = "import typing as _typing\n\n\ndef f(a: _typing.Any) -> None:\n    return None\n"
    after = _inserted(tmp_path, before, "a: Optional[int]", required=(("typing", "Optional"),))
    assert _new_names(before, after) == {HELPER}
    assert after.count("import typing") == 1, after


def test_a_public_or_rebound_typing_module_name_is_not_reused(tmp_path: Path) -> None:
    """``typing`` rebound later, and ``_typing`` taken by the program: neither is used."""
    before = "import typing\n_typing = 'taken'\n\n\ndef f() -> None:\n    typing = None\n"
    after = _inserted(tmp_path, before, "a: Any", required=(("typing", "Any"),))
    assert _new_names(before, after) == {HELPER, "_typing1"}
    assert f"def {HELPER}(a: _typing1.Any):" in after, after
    assert _public(after) == _public(before)


def test_a_runtime_import_of_anything_but_typing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RefactoringError, match="no private spelling"):
        _inserted(tmp_path, "import os\n", "a: Thing", required=(("pkg.other", "Thing"),))


# -- TYPE_CHECKING and the imports under it -----------------------------------------------


def test_a_type_only_import_is_private_and_its_guard_reads_a_private_typing(
    tmp_path: Path,
) -> None:
    before = "import os\n\n\ndef f() -> None:\n    return None\n"
    after = _inserted(tmp_path, before, "a: 'Thing'", checking=(("pkg.other", "Thing"),))
    assert _new_names(before, after) == {HELPER, "_Thing", "_typing"}
    _assert_private_and_free([HELPER, "_Thing", "_typing"], before)
    assert (
        "if _typing.TYPE_CHECKING:  # pragma: no cover\n"
        "    from pkg.other import Thing as _Thing\n" in after
    ), after
    assert f"def {HELPER}(a: '_Thing'):" in after, after
    assert _public(after) == _public(before)  # pkg.other does not exist: the guard never runs


@pytest.mark.parametrize(
    "binding",
    [
        # The semantic audit's P1-4: the module's own flag, named TYPE_CHECKING.
        "from os import curdir as TYPE_CHECKING",
        "TYPE_CHECKING = 'yes'",
        "from typing import TYPE_CHECKING\nTYPE_CHECKING = 'yes'",
    ],
)
def test_the_modules_own_type_checking_is_not_rebound(tmp_path: Path, binding: str) -> None:
    before = f"{binding}\n\nif TYPE_CHECKING:\n    MODE = 'debug'\nelse:\n    MODE = 'normal'\n"
    after = _inserted(tmp_path, before, "a: 'Thing'", checking=(("pkg.other", "Thing"),))
    assert _new_names(before, after) == {HELPER, "_Thing", "_typing"}
    assert _public(after) == _public(before) and _public(after)["MODE"] == "str 'debug'"


def test_a_relatively_imported_type_checking_is_not_taken_for_typings(tmp_path: Path) -> None:
    """``from .compat import TYPE_CHECKING`` may be anything; it cannot be run here, only read."""
    before = "from .compat import TYPE_CHECKING\n\n\ndef f() -> None:\n    return None\n"
    after = _inserted(tmp_path, before, "a: 'Thing'", checking=(("pkg.other", "Thing"),))
    assert _new_names(before, after) == {HELPER, "_Thing", "_typing"}
    assert "if _typing.TYPE_CHECKING:" in after, after


def test_typings_own_type_checking_bound_once_is_the_guard(tmp_path: Path) -> None:
    before = "from typing import TYPE_CHECKING\n\n\ndef f() -> None:\n    return None\n"
    after = _inserted(tmp_path, before, "a: 'Thing'", checking=(("pkg.other", "Thing"),))
    assert _new_names(before, after) == {HELPER, "_Thing"}
    assert "if TYPE_CHECKING:\n    from pkg.other import Thing as _Thing\n" in after, after


def test_a_name_the_checker_already_reads_from_the_guard_is_reused(tmp_path: Path) -> None:
    before = textwrap.dedent("""
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from pkg.other import Thing
        """)
    after = _inserted(tmp_path, before, "a: 'Thing'", checking=(("pkg.other", "Thing"),))
    assert _new_names(before, after) == {HELPER}
    assert f"def {HELPER}(a: 'Thing'):" in after, after


def test_a_type_only_name_the_module_binds_otherwise_gets_its_own_alias(tmp_path: Path) -> None:
    """For the checker the guard's import would replace the star-imported Thing too.

    A star import could rebind ``TYPE_CHECKING`` as well, so the module's guard
    is not one to join, and the new guard reads a private ``typing``.
    """
    before = textwrap.dedent("""
        from typing import TYPE_CHECKING
        from os.path import *
        Thing = 'the programs'
        if TYPE_CHECKING:
            from pkg.other import Thing
        """)
    after = _inserted(tmp_path, before, "a: 'Thing'", checking=(("pkg.other", "Thing"),))
    assert _new_names(before, after) == {HELPER, "_Thing", "_typing"}
    assert "    from pkg.other import Thing as _Thing\n" in after, after


def test_the_guard_writer_returns_the_private_name_it_bound() -> None:
    lines = ["import os\n"]
    bound = UnificationRefactorEngine()._ensure_type_checking_import(lines, "pkg.other", "Thing")
    assert bound == "_Thing"
    assert _new_names("import os\n", "".join(lines)) == {"_Thing", "_typing"}


# -- the helper's import into a module that calls it, and type declarations --------------


def test_a_helpers_import_binds_only_its_own_private_name() -> None:
    before = ["from .other import thing\n", "\n", "def f():\n", "    return thing\n"]
    lines = list(before)
    UnificationRefactorEngine()._ensure_import(lines, ".host", HELPER)
    assert _new_names("".join(before), "".join(lines)) == {HELPER}
    _assert_private_and_free([HELPER], "".join(before))


def test_type_declarations_bind_private_free_names_and_spell_their_bounds_privately(
    tmp_path: Path,
) -> None:
    declarations = """
        from typing import TypeVar as _towel_typevar
        _TowelT0 = _towel_typevar("_TowelT0", "Callable[[], int]", "str")
        """
    before = "import os\n\n\ndef f() -> None:\n    return None\n"
    after = _inserted(
        tmp_path,
        before,
        "a: '_TowelT0'",
        required=(("typing", "Callable"),),
        declarations=declarations,
    )
    assert _new_names(before, after) == {HELPER, "_TowelT0", "_towel_typevar", "_typing"}
    assert "_towel_typevar('_TowelT0', '_typing.Callable[[], int]', 'str')" in after, after
    _public(after)  # the declarations run


@pytest.mark.parametrize(
    "declarations",
    [
        'from typing import TypeVar as _towel_typevar\nT = _towel_typevar("T")\n',
        'from typing import TypeVar as _towel_typevar\n_taken = _towel_typevar("_taken")\n',
    ],
)
def test_a_public_or_taken_declaration_is_refused(tmp_path: Path, declarations: str) -> None:
    with pytest.raises(RefactoringError, match="generated declaration"):
        _inserted(tmp_path, "_taken = 1\n", "a: 'T'", declarations=declarations)


# -- the audit's reproductions, end to end under mypy --------------------------------------


def _run(root: Path, script: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
        check=False,
    )
    return completed.stdout + completed.stderr.strip().rsplit("\n", 1)[-1]


def _write(root: Path, files: Dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(textwrap.dedent(text), encoding="utf-8")


def _refactored(host: Path) -> str:
    """The first proposal for ``host`` applied under mypy, written back to it."""
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        proposals = engine.analyze_file(str(host))
        assert proposals
        changed = engine.apply_refactoring(str(host), proposals[0])
    finally:
        oracle.close()
    host.write_text(changed, encoding="utf-8")
    return changed


_TOTALS = """\
class Config:
    def __init__(self) -> None:
        self.scale = 2
        self.factor = 3


def f1(xs: list[int], cfg: Config) -> int:
    total = 0
    for x in xs:
        total += x * cfg.scale
        print("item", x)
    print("f1", total)
    return total


def f2(ys: list[int], cfg: Config) -> int:
    total = 0
    for y in ys:
        total += y * cfg.factor
        print("item", y)
    print("f2", total)
    return total
"""


@requires_mypy
def test_a_star_importers_callable_survives_the_thunks_annotation(tmp_path: Path) -> None:
    """The verification audit's D1: ``case Callable():`` raised once a.py imported typing's."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname = 'pkg'\nversion = '0'\n[tool.mypy]\n",
            "pkg/__init__.py": "",
            "pkg/a.py": _TOTALS,
            "pkg/b.py": """\
                from collections.abc import Callable
                from pkg.a import *


                def kind(value: object) -> str:
                    match value:
                        case Callable():
                            return "callable"
                        case _:
                            return "other"
                """,
        },
    )
    script = "from pkg import b\nprint(b.kind(len), b.kind(3), sorted(vars(b)))\n"
    before = _run(tmp_path, script)
    changed = _refactored(tmp_path / "pkg" / "a.py")
    assert before.startswith("callable other"), before
    assert _run(tmp_path, script) == before, changed
    assert "_typing.Callable[[], int]" in changed, changed


@requires_mypy
def test_the_modules_own_type_checking_flag_survives_a_type_only_import(tmp_path: Path) -> None:
    """The semantic audit's P1-4: ``from pkg.flags import DEBUG as TYPE_CHECKING`` became False."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname = 'pkg'\nversion = '0'\n[tool.mypy]\nstrict = true\n",
            "pkg/__init__.py": "",
            "pkg/flags.py": "DEBUG = True\n",
            "pkg/w.py": """\
                class Widget:
                    def __init__(self) -> None:
                        self.size = 3

                    def poke(self, k: int) -> None:
                        print("poke", k)
                """,
            "pkg/f.py": """\
                from __future__ import annotations

                from typing import TYPE_CHECKING

                if TYPE_CHECKING:
                    from pkg.w import Widget


                def make() -> Widget:
                    from pkg.w import Widget as W

                    return W()
                """,
            "pkg/m.py": """\
                import pkg
                from pkg.f import make
                from pkg.flags import DEBUG as TYPE_CHECKING

                if TYPE_CHECKING:
                    MODE = "debug"
                else:
                    MODE = "normal"


                def f1(k: int) -> int:
                    x = make()
                    if k > 100:
                        return 0
                    x.poke(k)
                    n = x.size + k
                    print("f1", n)
                    return n


                def f2(k: int) -> int:
                    x = make()
                    k = k * 2
                    x.poke(k)
                    n = x.size + k
                    print("f2", n)
                    return n * 2
                """,
        },
    )
    script = "import pkg.m as m\nprint(m.MODE, m.TYPE_CHECKING, m.f1(1), m.f2(1))\n"
    before = _run(tmp_path, script)
    changed = _refactored(tmp_path / "pkg" / "m.py")
    assert "debug True" in before, before
    assert _run(tmp_path, script) == before, changed
    assert "from pkg.w import Widget as _Widget" in changed, changed
