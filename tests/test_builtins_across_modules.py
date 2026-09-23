"""A builtin that a borrowed helper reads is the same lookup from every module, or the pair is declined.

A bare name in a function is looked up in its module, then in the builtins.
A helper hosted in ``reports`` that ``exports`` borrows reads ``len`` in
``reports``, where the block it replaced read ``exports``: a test patching
``pkg.exports.len``, which ``mock`` does for a builtin without being asked
to create it, stopped reaching ``exports`` (the audit's reproducer printed
10001 before refactoring and 5 after). Passing ``len`` from each caller would
keep the lookup, but no helper takes a builtin as a parameter; a pair is
declined instead wherever the program shows a participating module may hold
the name: a binding of its own, a star import that reaches it, a write into
its namespace at run time, or a patch in the project's own code or tests.
Module names are still passed, so a patch of one is still honoured.
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
            "from pkg import reports  # exports already depends on reports\n" + exports_prelude,
            'def export_size(rows, name):\n    print("exports", name)\n    ',
        ),
        "pkg/reports.py": _module(
            reports_prelude, 'def report_size(rows, name):\n    print("reports")\n    '
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


def _refactor(project: Path) -> Tuple[int, Mapping[str, int]]:
    """Refactor ``project/pkg`` in place; how many refactorings applied, and why pairs were declined."""
    # Every pair here spans modules, which is opt-in (docs/DECISIONS.md).
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
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
from unittest import mock
import pkg.exports
with mock.patch("pkg.exports.len", lambda value: 100, create=True):
    print("exports with its len patched:", pkg.exports.export_size([1, 2], "ab"))
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


def test_without_evidence_the_helper_reads_the_builtin_bare(tmp_path: Path) -> None:
    files = _project()
    project = tmp_path / "proj"
    _write(project, files)
    _write(tmp_path / "original", files)
    applied, _ = _refactor(project)
    assert applied > 0
    helper = _helper(project / "pkg")
    assert [argument.arg for argument in helper.args.args] == ["name", "rows"]
    assert not {argument.arg for argument in helper.args.args} & BUILTIN_NAMES
    driver = "import pkg.exports, pkg.reports\nprint(pkg.exports.export_size([1, 2], 'ab'))\n"
    (tmp_path / "drive.py").write_text(driver + "print(pkg.reports.report_size([1], 'abc'))\n")
    assert _run(tmp_path, "drive.py", pythonpath="proj") == _run(
        tmp_path, "drive.py", pythonpath="original"
    )


# Evidence in a participating module, and the reason the pair is declined for.
# A site's module that reaches its namespace dynamically, or declares a name
# ``global``, is declined before placement asks: the block's external reads
# may be rebound between the call and the read (``rebound_external_binding``).
IN_MODULE = {
    "defined_in_the_borrower": ("def len(item):\n    return 7", "", "builtin_may_differ_by_module"),
    "defined_in_the_host": ("", "def len(item):\n    return 7", "builtin_may_differ_by_module"),
    "a_class_of_its_name": ("class len:\n    pass", "", "builtin_may_differ_by_module"),
    "imported_under_its_name": ("from builtins import len", "", "builtin_may_differ_by_module"),
    # Module data a callback may rebind is declined before placement asks.
    "assigned_at_the_top_level": ("len = lambda item: 7", "", "module_data_lookup"),
    "declared_global_in_a_function": (
        "def reset():\n    global len\n    len = lambda item: 7",
        "",
        "rebound_external_binding",
    ),
    "globals_subscript": ('globals()["len"] = lambda item: 7', "", "rebound_external_binding"),
    "vars_subscript": ('vars()["len"] = lambda item: 7', "", "builtin_may_differ_by_module"),
    "globals_update": ("globals().update(len=lambda item: 7)", "", "rebound_external_binding"),
    "globals_handed_on": (
        "def install(namespace):\n    namespace['len'] = lambda item: 7\n\n\ninstall(globals())",
        "",
        "rebound_external_binding",
    ),
    "setattr_of_its_own_module": (
        'import sys\nsetattr(sys.modules[__name__], "len", lambda item: 7)',
        "",
        "rebound_external_binding",
    ),
    "exec_of_code": ('exec("len = lambda item: 7")', "", "rebound_external_binding"),
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


# What a module's own code may write into its namespace, found by the evidence
# scan whatever guard declines the pair first.
WRITES_ITS_OWN_NAMESPACE = {
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
    "declared_global": "def reset():\n    global len\n    len = f",
    "defined": "def len(item):\n    return 7",
    "star_import_outside_the_project": "from os.path import *",
    "rebound_builtins": "__builtins__ = {}",
}

READS_ITS_OWN_NAMESPACE = {
    "globals_read": 'x = globals()["len"]',
    "globals_get": 'x = globals().get("len")',
    "membership": 'found = "len" in globals()',
    "sorted_names": "names = sorted(globals())",
    "loop": "for name in globals():\n    print(name)",
    "vars_in_a_function": 'def f():\n    vars()["len"] = f',
    "locals_in_a_function": 'def f():\n    locals()["len"] = f',
    "exec_in_a_fresh_namespace": 'exec("len = f", {})',
    "another_name": 'globals()["width"] = f',
}


def _evidence(tmp_path: Path, source: str) -> Optional[str]:
    """Why the module ``source`` may hold ``len``, or None."""
    _write(tmp_path, {"pyproject.toml": "", "pkg/__init__.py": "", "pkg/mod.py": source})
    module = (tmp_path / "pkg" / "mod.py").resolve()
    return builtin_rebinding(module, source, {"len"}, scan_project_writes(tmp_path)).get("len")


@pytest.mark.parametrize("case", sorted(WRITES_ITS_OWN_NAMESPACE))
def test_a_module_writing_its_namespace_may_hold_the_builtin(tmp_path: Path, case: str) -> None:
    assert _evidence(tmp_path, WRITES_ITS_OWN_NAMESPACE[case]) is not None


@pytest.mark.parametrize("case", sorted(READS_ITS_OWN_NAMESPACE))
def test_a_module_reading_its_namespace_holds_no_builtin(tmp_path: Path, case: str) -> None:
    assert _evidence(tmp_path, READS_ITS_OWN_NAMESPACE[case]) is None


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
    applied, _ = _refactor(project)
    assert applied > 0
    assert "len" not in {argument.arg for argument in _helper(project / "pkg").args.args}


PATCHES = {
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
def test_a_patch_in_the_projects_tests_declines_the_pair(tmp_path: Path, case: str) -> None:
    files = _project(extra={"tests/test_exports.py": PATCHES[case]})
    _assert_declined(tmp_path, files, "builtin_may_differ_by_module")


def test_a_relative_import_in_a_test_package_is_followed(tmp_path: Path) -> None:
    files = _project(
        extra={
            "pkg/tests/__init__.py": "",
            "pkg/tests/test_exports.py": """
                from .. import exports

                def test_size(monkeypatch):
                    monkeypatch.setattr(exports, "len", lambda value: 100, raising=False)
                """,
        }
    )
    _assert_declined(tmp_path, files, "builtin_may_differ_by_module")


NOT_EVIDENCE = {
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
def test_a_patch_elsewhere_leaves_the_pair_alone(tmp_path: Path, case: str) -> None:
    project = tmp_path / "proj"
    _write(project, _project(extra={"tests/test_other.py": NOT_EVIDENCE[case]}))
    applied, _ = _refactor(project)
    assert applied > 0
    assert not {argument.arg for argument in _helper(project / "pkg").args.args} & BUILTIN_NAMES


MODULE_NAME_BLOCK = """
    width = join(name, "x")
    height = join(rows, "y")
    area = width + height
    return area + "!"
"""

MODULE_NAME_CHECK = """
from unittest import mock
import pkg.exports, pkg.reports

for module in ("exports", "reports"):
    with mock.patch("pkg." + module + ".join", lambda *parts: "J"):
        print(module, pkg.exports.export_size("r", "n"), pkg.reports.report_size("r", "n"))
"""


def test_a_module_name_is_still_passed_so_patching_it_reaches_each_caller(
    tmp_path: Path,
) -> None:
    files = _project("from os.path import join", "from os.path import join")
    for path in ("pkg/exports.py", "pkg/reports.py"):
        files[path] = files[path].replace(
            textwrap.dedent(BLOCK).strip("\n").replace("\n", "\n    "),
            textwrap.dedent(MODULE_NAME_BLOCK).strip("\n").replace("\n", "\n    "),
        )
    files["check.py"] = MODULE_NAME_CHECK
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    applied, _ = _refactor(after)
    assert applied > 0
    assert "join" in {argument.arg for argument in _helper(after / "pkg").args.args}
    assert _run(after, "check.py") == _run(before, "check.py")


SAME_MODULE = """
def first(rows, name):
    print("first", name)
    width = len(name)
    height = len(rows)
    area = width * height
    return area + 1


def second(rows, name):
    print("second")
    width = len(name)
    height = len(rows)
    area = width * height
    return area + 1
"""

SAME_MODULE_CHECK = """
from unittest import mock
import m

with mock.patch("m.len", lambda value: 100):
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
    assert [argument.arg for argument in helpers[0].args.args] == ["name", "rows"]
    assert _run(after, "check.py") == _run(before, "check.py")
