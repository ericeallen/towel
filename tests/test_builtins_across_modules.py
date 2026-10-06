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

"""Cross-module builtin lookups stay at their caller or the pair is declined.

Ordinary external code can rebind a module attribute without exposing that
write to Towel. Default cross-module extraction therefore declines builtin
reads; the explicit opt-in preserves each lookup with a caller-side thunk.
Reflective operations add no checks or preservation guarantee.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, Mapping, Optional, Tuple

import pytest

from tests.test_helpers import module_functions, refactor_to_fixed_point_silently
from towel.unification.builtins import BUILTIN_NAMES
from towel.unification.namespace_writes import builtin_rebinding, scan_project_writes
from towel.unification.refactor_engine import UnificationRefactorEngine

BLOCK = """
    width = len(name)
    height = len(rows)
    area = width * height
    return area + 1
"""


def _module(prelude: str, header: str) -> str:
    parts = [textwrap.dedent(prelude).strip()] if prelude.strip() else []
    parts.append(header + textwrap.dedent(BLOCK).strip("\n").replace("\n", "\n    ") + "\n")
    return "\n\n\n".join(parts)


def _project(
    exports_prelude: str = "", reports_prelude: str = "", extra: Mapping[str, str] = {}
) -> Dict[str, str]:
    """``exports`` already imports ``reports``, so ``reports`` hosts and ``exports`` borrows."""
    return {
        "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
        "pkg/__init__.py": "",
        "pkg/exports.py": _module(
            "import pkg.reports as reports  # exports already depends on reports\n"
            + exports_prelude,
            'def export_size(rows, name):\n    print("exports", name)\n    ',
        ),
        "pkg/reports.py": _module(
            reports_prelude, 'def report_size(rows, name):\n    print("reports", name)\n    '
        ),
        **{path: textwrap.dedent(text).lstrip("\n") for path, text in extra.items()},
    }


def _write(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _python_files(root: Path) -> Dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.py"))}


def _refactor(
    project: Path, *, parameterize_builtins: bool = False
) -> Tuple[int, Mapping[str, int]]:
    """Refactor ``project/pkg`` in place; how many refactorings applied, and why pairs were declined."""
    # Every pair here spans modules, which is opt-in (docs/DECISIONS.md).
    engine = UnificationRefactorEngine(
        # Full-body sharing preserves the header effect and needs one literal
        # tag, two builtin lookups, two arguments and the ownership holder.
        min_lines=3,
        max_parameters=6,
        cross_module_helpers=True,
        parameterize_builtins=parameterize_builtins,
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(project / "pkg"), str(project / "pkg"), progress="none"
        )
    return sum(applied for applied, _ in results.values()), engine.declined_pairs


def _run(cwd: Path, script: str, *, pythonpath: str = "") -> str:
    completed = subprocess.run(
        [sys.executable, script],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": pythonpath},
    )
    return completed.stdout


def _helper(package: Path) -> ast.FunctionDef:
    """The one helper, which one module defines and the other imports."""
    found = [
        (path, function)
        for path in (package / "exports.py", package / "reports.py")
        for name, function in module_functions(path.read_text()).items()
        if name.startswith("__extracted_func")
    ]
    assert len(found) == 1, found
    host, helper = found[0]
    borrower = package / ("reports.py" if host.name == "exports.py" else "exports.py")
    assert f"import {helper.name}" in borrower.read_text()
    return helper


def _assert_declined(tmp_path: Path, files: Mapping[str, str], reason: str) -> None:
    project = tmp_path / "proj"
    _write(project, files)
    before = _python_files(project)
    applied, declined = _refactor(project)
    assert applied == 0
    assert _python_files(project) == before
    assert reason in declined, declined


REPRODUCER_CHECK = """
import pkg.exports
pkg.exports.len = lambda value: 100
print("exports with its len rebound:", pkg.exports.export_size([1, 2], "ab"))
"""


def test_the_reproducer_is_declined_and_keeps_its_behaviour(tmp_path: Path) -> None:
    files = _project(extra={"check.py": REPRODUCER_CHECK})
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    applied, declined = _refactor(after)
    assert applied == 0
    assert "builtin_may_differ_by_module" in declined
    assert _run(after, "check.py") == _run(before, "check.py")
    assert "10001" in _run(after, "check.py")


def test_no_visible_write_does_not_make_modules_share_a_builtin_lookup(tmp_path: Path) -> None:
    _assert_declined(tmp_path, _project(), "builtin_may_differ_by_module")


# Evidence in a participating module, and the reason the pair is declined for.
# An ordinary global declaration is declined before placement asks because
# the block's external reads may be rebound between the call and the read.
# Reflective namespace access is excluded from this evidence.
IN_MODULE = {
    "defined_in_the_borrower": ("def len(item):\n    return 7", "", "builtin_may_differ_by_module"),
    "defined_in_the_host": ("", "def len(item):\n    return 7", "builtin_may_differ_by_module"),
    "a_class_of_its_name": ("class len:\n    pass", "", "builtin_may_differ_by_module"),
    "imported_under_its_name": ("from builtins import len", "", "builtin_may_differ_by_module"),
    "assigned_at_the_top_level": ("len = lambda item: 7", "", "module_data_lookup"),
    "declared_global_in_a_function": (
        "def reset():\n    global len\n    len = lambda item: 7",
        "",
        "rebound_external_binding",
    ),
    "star_import_outside_the_project": (
        "from os.path import *",
        "",
        "builtin_may_differ_by_module",
    ),
    "rebound_builtins_namespace": (
        "import builtins\n__builtins__ = dict(vars(builtins), len=lambda item: 7)",
        "",
        "builtin_may_differ_by_module",
    ),
}


@pytest.mark.parametrize("case", sorted(IN_MODULE))
def test_evidence_in_a_participating_module_declines_the_pair(tmp_path: Path, case: str) -> None:
    exports_prelude, reports_prelude, reason = IN_MODULE[case]
    _assert_declined(tmp_path, _project(exports_prelude, reports_prelude), reason)


# Ordinary bindings supply evidence; reflective namespace operations do not.
WRITES_ITS_OWN_NAMESPACE = {
    "declared_global": "def reset():\n    global len\n    len = f",
    "defined": "def len(item):\n    return 7",
    "star_import_outside_the_project": "from os.path import *",
    "rebound_builtins": "__builtins__ = {}",
    "direct_imported_attribute": "import pkg.mod as module\nmodule.len = f",
}

REFLECTIVE_NAMESPACE_OPERATIONS = {
    "globals_read": 'x = globals()["len"]',
    "globals_get": 'x = globals().get("len")',
    "membership": 'found = "len" in globals()',
    "sorted_names": "names = sorted(globals())",
    "loop": "for name in globals():\n    print(name)",
    "vars_in_a_function": 'def f():\n    vars()["len"] = f',
    "locals_in_a_function": 'def f():\n    locals()["len"] = f',
    "exec_in_a_fresh_namespace": 'exec("len = f", {})',
    "another_name": 'globals()["width"] = f',
    "globals_subscript": 'globals()["len"] = f',
    "globals_update": "globals().update(len=f)",
    "globals_setdefault": 'globals().setdefault("len", f)',
    "globals_handed_on": "install(globals())",
    "globals_aliased": 'namespace = globals()\nnamespace["len"] = f',
    "vars_at_the_top_level": 'vars()["len"] = f',
    "locals_at_the_top_level": 'locals()["len"] = f',
    "setattr_of_its_own_module": 'import sys\nsetattr(sys.modules[__name__], "len", f)',
    "own_module_attribute": "import sys\nsys.modules[__name__].len = f",
    "exec": 'exec("len = f")',
    "exec_in_its_namespace": "exec(code, globals())",
    "module_dict_subscript": 'import pkg.mod\npkg.mod.__dict__["len"] = f',
    "module_dict_update": "import pkg.mod\npkg.mod.__dict__.update(len=f)",
    "module_vars_update": "import pkg.mod\nvars(pkg.mod).update(len=f)",
    "dict_monkeypatch": 'import pkg.mod\nmonkeypatch.setitem(pkg.mod.__dict__, "len", f)',
    "sys_modules_attribute": 'import sys\nsys.modules["pkg.mod"].len = f',
    "getattr_attribute": 'import pkg.mod\ngetattr(pkg, "mod").len = f',
    "eval": "eval(code)",
    "namespace_clear": "globals().clear()",
}


def _evidence(tmp_path: Path, source: str) -> Optional[str]:
    """Why the module ``source`` may hold ``len``, or None."""
    _write(tmp_path, {"pyproject.toml": "", "pkg/__init__.py": "", "pkg/mod.py": source})
    module = (tmp_path / "pkg" / "mod.py").resolve()
    return builtin_rebinding(module, source, {"len"}, scan_project_writes(tmp_path)).get("len")


@pytest.mark.parametrize("case", sorted(WRITES_ITS_OWN_NAMESPACE))
def test_a_module_writing_its_namespace_may_hold_the_builtin(tmp_path: Path, case: str) -> None:
    assert _evidence(tmp_path, WRITES_ITS_OWN_NAMESPACE[case]) is not None


@pytest.mark.parametrize("case", sorted(REFLECTIVE_NAMESPACE_OPERATIONS))
def test_reflection_does_not_supply_builtin_rebinding_evidence(tmp_path: Path, case: str) -> None:
    assert _evidence(tmp_path, REFLECTIVE_NAMESPACE_OPERATIONS[case]) is None


def test_a_star_import_is_followed_into_the_project(tmp_path: Path) -> None:
    binding = _project(
        "from pkg.util import *",
        extra={"pkg/util.py": "def len(item):\n    return 55\n"},
    )
    _assert_declined(tmp_path / "binding", binding, "builtin_may_differ_by_module")
    listed = _project(
        "from .util import *",
        extra={"pkg/util.py": '__all__ = ["len"]\n\n\ndef len(item):\n    return 55\n'},
    )
    _assert_declined(tmp_path / "listed", listed, "builtin_may_differ_by_module")
    elsewhere = _project(
        "from pkg.util import *",
        extra={"pkg/util.py": "def measure(item):\n    return 55\n\n\n_len = len\n"},
    )
    project = tmp_path / "elsewhere" / "proj"
    _write(project, elsewhere)
    applied, declined = _refactor(project)
    assert applied == 0
    assert "builtin_may_differ_by_module" in declined


PATCHES = {
    "attribute_assignment": """
        from pkg import exports

        exports.len = lambda value: 100
        """,
    "annotated_attribute_assignment": """
        from typing import Callable
        from pkg import exports

        exports.len: Callable[[object], int] = lambda value: 100
        """,
    "unpacked_into_the_attribute": """
        from pkg import exports

        exports.len, spare = (lambda value: 100), None
        """,
    "tuple_last_attribute": (
        "import importlib\nmodule = importlib.import_module('pkg.exports')\n"
        "(other, module.len) = (None, replacement)"
    ),
    "list_last_attribute": "import pkg.exports\n[other, pkg.exports.len] = [None, replacement]",
    "parenthesized_attribute": "import pkg.exports\n(pkg.exports.len) = replacement",
    "continued_attribute": "import pkg.exports\npkg.exports.\\\nlen = replacement",
    "deleted_attribute": "import pkg.exports\ndel pkg.exports.len",
    "augmented_attribute": "import pkg.exports\npkg.exports.len += extra",
    "import_module_attribute": "from importlib import import_module\n"
    'module = import_module("pkg.exports")\n'
    "module.len = replacement",
    "aliased_module_attribute": "import pkg.exports as original\n"
    "alias = original\n"
    "alias.len = replacement",
}


REFLECTIVE_PATCHES = {
    "mock_patch_creating_the_name": """
        from unittest import mock
        import pkg.exports

        def test_size():
            with mock.patch("pkg.exports.len", lambda value: 100, create=True):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "mock_patch_without_create": """
        from unittest import mock
        import pkg.exports

        def test_size():
            with mock.patch("pkg.exports.len", lambda value: 100):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "unittest_mock_patch": """
        import unittest.mock
        import pkg.exports

        def test_size():
            with unittest.mock.patch("pkg.exports.len", lambda value: 100):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "patch_as_a_decorator": """
        from unittest.mock import patch
        import pkg.exports

        @patch("pkg.exports.len", lambda value: 100)
        def test_size():
            assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "patch_object_through_the_package": """
        from unittest import mock
        import pkg.exports

        def test_size():
            with mock.patch.object(pkg.exports, "len", lambda value: 100, create=True):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "patch_object_of_an_imported_module": """
        from unittest.mock import patch
        from pkg import exports

        def test_size():
            with patch.object(exports, "len", lambda value: 100, create=True):
                assert exports.export_size([1, 2], "ab") == 10001
        """,
    "patch_object_of_import_module": """
        import importlib
        from unittest import mock

        exports = importlib.import_module("pkg.exports")

        def test_size():
            with mock.patch.object(exports, "len", lambda value: 100, create=True):
                assert exports.export_size([1, 2], "ab") == 10001
        """,
    "patch_multiple": """
        from unittest import mock
        import pkg.exports

        def test_size():
            with mock.patch.multiple("pkg.exports", len=lambda value: 100, create=True):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "patch_dict_of_the_namespace": """
        from unittest import mock
        import pkg.exports

        def test_size():
            with mock.patch.dict(pkg.exports.__dict__, {"len": lambda value: 100}):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "monkeypatch_setattr_by_string": """
        import pkg.exports

        def test_size(monkeypatch):
            monkeypatch.setattr("pkg.exports.len", lambda value: 100, raising=False)
            assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "monkeypatch_setattr_of_the_module": """
        from pkg import exports

        def test_size(monkeypatch):
            monkeypatch.setattr(exports, "len", lambda value: 100, raising=False)
            assert exports.export_size([1, 2], "ab") == 10001
        """,
    "monkeypatch_setitem_of_the_namespace": """
        import pkg.exports as exports

        def test_size(monkeypatch):
            monkeypatch.setitem(exports.__dict__, "len", lambda value: 100)
            assert exports.export_size([1, 2], "ab") == 10001
        """,
    "setattr": """
        from pkg import exports

        setattr(exports, "len", lambda value: 100)
        """,
    "target_spelled_from_a_constant": """
        from unittest import mock
        import pkg.exports

        MODULE = "pkg.exports"

        def test_size():
            with mock.patch(f"{MODULE}.len", lambda value: 100, create=True):
                assert pkg.exports.export_size([1, 2], "ab") == 10001
        """,
    "target_concatenated": """
        from unittest import mock

        PACKAGE = "pkg"
        MODULE = PACKAGE + ".exports"

        @mock.patch(MODULE + ".len", lambda value: 100, create=True)
        def test_size():
            pass
        """,
    "module_part_computed": """
        import pytest
        from unittest import mock

        @pytest.mark.parametrize("module", ["exports", "reports"])
        def test_size(module):
            with mock.patch("pkg." + module + ".len", lambda value: 100, create=True):
                pass
        """,
    "import_module_of_a_constant": """
        import importlib

        NAME = "pkg.exports"

        def test_size(monkeypatch):
            exports = importlib.import_module(NAME)
            monkeypatch.setattr(exports, "len", lambda value: 100, raising=False)
        """,
    "getattr_of_the_package": """
        import pkg.exports

        def test_size(monkeypatch):
            monkeypatch.setattr(getattr(pkg, "exports"), "len", lambda value: 100, raising=False)
        """,
    "patching_the_host": """
        from unittest import mock
        import pkg.reports

        def test_size():
            with mock.patch("pkg.reports.len", lambda value: 100, create=True):
                assert pkg.reports.report_size([1, 2], "ab") == 10001
        """,
}


@pytest.mark.parametrize("case", sorted(PATCHES))
def test_direct_attribute_binding_in_project_tests_declines_the_pair(
    tmp_path: Path, case: str
) -> None:
    files = _project(extra={"tests/test_exports.py": PATCHES[case]})
    _assert_declined(tmp_path, files, "builtin_may_differ_by_module")


def test_a_relative_import_in_a_test_package_is_followed(tmp_path: Path) -> None:
    files = _project(
        extra={
            "pkg/tests/__init__.py": "",
            "pkg/tests/test_exports.py": """
                from .. import exports

                def test_size():
                    exports.len = lambda value: 100
                """,
        }
    )
    _assert_declined(tmp_path, files, "builtin_may_differ_by_module")


NOT_EVIDENCE = {
    "ordinary_other_module": "import pkg.other\npkg.other.len = replacement\n",
    "ordinary_other_attribute": "def install(module):\n    module.width = replacement\n",
    "ordinary_attribute_read": "def read(module):\n    return module.len\n",
    "ordinary_unassigned_annotation": "def annotate(module):\n    module.len: object\n",
    "another_module": 'from unittest import mock\nmock.patch("pkg.other.len", create=True)\n',
    "another_name": 'from unittest import mock\nmock.patch("pkg.exports.width", create=True)\n',
    "the_builtins_module": 'from unittest import mock\nmock.patch("builtins.len", len)\n',
    "reading_the_namespace": 'import pkg.exports\nprint(sorted(vars(pkg.exports)), "len")\n',
    "computed_module_another_name": (
        "from unittest import mock\n"
        'for module in ("exports", "reports"):\n'
        '    mock.patch("pkg." + module + ".width", create=True)\n'
    ),
    "a_constant_rebound": (
        'from unittest import mock\nMODULE = "pkg.exports"\nMODULE = "pkg.other"\n'
        'mock.patch(MODULE + ".width", create=True)\n'
    ),
}


@pytest.mark.parametrize("case", sorted(NOT_EVIDENCE))
def test_unrelated_or_reflective_writes_leave_the_pair_alone(tmp_path: Path, case: str) -> None:
    project = tmp_path / "proj"
    _write(project, _project(extra={"tests/test_other.py": NOT_EVIDENCE[case]}))
    applied, _ = _refactor(project, parameterize_builtins=True)
    assert applied > 0
    assert not {argument.arg for argument in _helper(project / "pkg").args.args} & BUILTIN_NAMES


MODULE_NAME_BLOCK = """
    width = join(name, "x")
    height = join(rows, "y")
    area = width + height
    return area + "!"
"""

MODULE_NAME_CHECK = """
import pkg.exports, pkg.reports

pkg.exports.join = lambda *parts: "J"
print("exports", pkg.exports.export_size("r", "n"), pkg.reports.report_size("r", "n"))
pkg.reports.join = lambda *parts: "K"
print("reports", pkg.exports.export_size("r", "n"), pkg.reports.report_size("r", "n"))
"""


def test_a_module_name_is_still_passed_so_patching_it_reaches_each_caller(
    tmp_path: Path,
) -> None:
    # This test targets an imported module binding, not the separate builtin
    # opt-in. Keep its observable header through the imported print alias.
    prelude = "from os.path import join\nfrom builtins import print as emit"
    files = _project(prelude, prelude)
    for path in ("pkg/exports.py", "pkg/reports.py"):
        files[path] = (
            files[path]
            .replace(
                textwrap.dedent(BLOCK).strip("\n").replace("\n", "\n    "),
                textwrap.dedent(MODULE_NAME_BLOCK).strip("\n").replace("\n", "\n    "),
            )
            .replace("    print(", "    emit(")
        )
    files["check.py"] = MODULE_NAME_CHECK
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    applied, _ = _refactor(after)
    assert applied > 0
    assert any(
        isinstance(node, ast.Lambda) and isinstance(node.body, ast.Name) and node.body.id == "join"
        for path in (after / "pkg").glob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
    )
    assert _run(after, "check.py") == _run(before, "check.py")


SAME_MODULE = """
def first(rows, name):
    print("first", name)
    width = len(name)
    height = len(rows)
    area = width * height
    return area + 1


def second(rows, name):
    print("second", name)
    width = len(name)
    height = len(rows)
    area = width * height
    return area + 1
"""

SAME_MODULE_CHECK = """
import m

m.len = lambda value: 100
print(m.first([1, 2], "ab"), m.second([1, 2], "ab"))
"""


def test_a_same_module_helper_is_unchanged_by_a_patch_of_its_module(tmp_path: Path) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": SAME_MODULE, "check.py": SAME_MODULE_CHECK})
    final, applied = refactor_to_fixed_point_silently(str(after / "m.py"), min_lines=3)
    assert applied == 1
    (after / "m.py").write_text(final)
    helpers = [f for name, f in module_functions(final).items() if name.startswith("__extracted")]
    # The complete body also carries its header tag and final argument owner;
    # len remains an ordinary lookup in this same module.
    assert [argument.arg for argument in helpers[0].args.args] == [
        "__param_0",
        "name",
        "rows",
        "_towel_owner",
    ]
    assert (
        sum(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
            for node in ast.walk(helpers[0])
        )
        == 2
    )
    assert _run(after, "check.py") == _run(before, "check.py")


@pytest.mark.parametrize("case", sorted(REFLECTIVE_PATCHES))
def test_reflective_patching_does_not_refuse_extraction(tmp_path: Path, case: str) -> None:
    project = tmp_path / "proj"
    _write(project, _project(extra={"tests/test_exports.py": REFLECTIVE_PATCHES[case]}))
    applied, declined = _refactor(project, parameterize_builtins=True)
    assert applied > 0
    assert "builtin_may_differ_by_module" not in declined


REFLECTIVE_PRELUDES = {
    "globals_subscript": 'globals()["len"] = lambda item: 7',
    "vars_subscript": 'vars()["len"] = lambda item: 7',
    "globals_update": "globals().update(len=lambda item: 7)",
    "globals_handed_on": "def install(namespace):\n"
    "    namespace['len'] = lambda item: 7\n"
    "\n"
    "\n"
    "install(globals())",
    "setattr_of_its_own_module": "import sys\n"
    'setattr(sys.modules[__name__], "len", lambda item: 7)',
    "exec_of_code": 'exec("len = lambda item: 7")',
}


@pytest.mark.parametrize("case", sorted(REFLECTIVE_PRELUDES))
def test_reflection_in_a_participating_module_does_not_refuse_extraction(
    tmp_path: Path, case: str
) -> None:
    project = tmp_path / "proj"
    _write(project, _project(REFLECTIVE_PRELUDES[case]))
    applied, declined = _refactor(project, parameterize_builtins=True)
    assert applied > 0
    assert "builtin_may_differ_by_module" not in declined


def test_reflection_operations_do_not_trigger_the_namespace_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from towel.unification import namespace_writes

    _write(
        tmp_path,
        {
            "reflection.py": "import pkg.mod\n"
            "globals().update(len=f)\n"
            "exec(code)\n"
            "mock.patch(target, f)\n"
            "setattr(pkg.mod, 'len', f)\n"
            "pkg.mod.__dict__['len'] = f\n",
        },
    )

    def unexpected_parse(source: object, *, filename: str) -> ast.Module:
        raise AssertionError(f"reflection-only input reached parser: {filename}")

    monkeypatch.setattr(namespace_writes, "parse_analysis_source", unexpected_parse)
    writes = namespace_writes.scan_project_writes(tmp_path)
    assert writes.complete
    assert not writes.by_path and not writes.by_name
