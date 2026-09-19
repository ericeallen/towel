"""Release-audit regressions compare actual caller behavior across rename plans."""

from pathlib import Path
import subprocess
import sys

import pytest

from towel.changes import apply_changes
from towel.renaming import plan_renames

HELPER = "def __extracted_func_0(__param_0):\n    return __param_0 + 1\n"


def _project(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _run(root: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(root / "main.py")], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "files,specification",
    [
        (
            {"main.py": HELPER + "print(__extracted_func_0(__param_0=3))\n"},
            "__extracted_func_0.__param_0",
        ),
        (
            {
                "a.py": HELPER,
                "b.py": "from a import __extracted_func_0 as exported\n",
                "main.py": "from b import exported as use\nprint(use(__param_0=3))\n",
            },
            "__extracted_func_0.__param_0",
        ),
        (
            {
                "pkg/__init__.py": "",
                "pkg/a.py": HELPER,
                "main.py": "import pkg.a\nprint(pkg.a.__extracted_func_0(__param_0=3))\n",
            },
            "__extracted_func_0.__param_0",
        ),
        (
            {
                "main.py": "class _A:\n    def _extracted_func_0(self, __param_0):\n        return __param_0 + 1\nprint(_A()._extracted_func_0(_A__param_0=3))\n"
            },
            "_extracted_func_0.__param_0",
        ),
        (
            {
                "main.py": "class A:\n    @staticmethod\n    def _extracted_func_0(argument):\n        return argument+1\nprint(A._extracted_func_0(argument=3))\n"
            },
            "_extracted_func_0.argument",
        ),
        (
            {
                "main.py": "def __extracted_func_0(__param_0, /, **kwargs):\n    return __param_0, kwargs\nprint(__extracted_func_0(3, __param_0=4))\n"
            },
            "__extracted_func_0.__param_0",
        ),
    ],
    ids=[
        "direct",
        "reexport",
        "qualified-module",
        "mangled-keyword",
        "static-method",
        "positional-only",
    ],
)
def test_resolved_keyword_consumers_keep_behavior(
    tmp_path: Path, files: dict[str, str], specification: str
) -> None:
    _project(tmp_path, files)
    before = _run(tmp_path)
    plan, changes = plan_renames(tmp_path, [(specification, "value", None)])
    assert changes > 0
    apply_changes(plan)
    assert _run(tmp_path) == before


@pytest.mark.parametrize(
    "extra",
    [
        "from a import __extracted_func_0 as use\nfrom b import other as use\nprint(use(__param_0=3))\n",
        "from a import __extracted_func_0 as use\nuse = lambda __param_0: __param_0\nprint(use(__param_0=3))\n",
        "if True:\n    import a as chosen\nelse:\n    import b as chosen\nprint(chosen.__extracted_func_0(__param_0=3))\n",
        "import bridge\nalias = bridge\nprint(alias.exported(__param_0=3))\n",
        "from a import *\nprint(__extracted_func_0(__param_0=3))\n",
        "from a import __extracted_func_0\nprint(__extracted_func_0(**{'__param_0': 3}))\n",
        "from a import __extracted_func_0\nalias = __extracted_func_0\nprint(alias(__param_0=3))\n",
    ],
    ids=[
        "import-rebinding",
        "assigned-rebinding",
        "conditional-module",
        "module-escape",
        "star-import",
        "keyword-dictionary",
        "callable-escape",
    ],
)
def test_unresolved_keyword_consumers_refuse_without_writes(tmp_path: Path, extra: str) -> None:
    files = {
        "a.py": HELPER,
        "b.py": "def other(__param_0):\n    return __param_0 + 100\n",
        "bridge.py": "from a import __extracted_func_0 as exported\n",
        "main.py": extra,
    }
    _project(tmp_path, files)
    before = {path: path.read_bytes() for path in tmp_path.glob("*.py")}
    with pytest.raises(ValueError):
        plan_renames(tmp_path, [("__extracted_func_0.__param_0", "value", tmp_path / "a.py")])
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize(
    "name,consumer",
    [
        ("len", "print(len([1,2]))"),
        ("int", "def f(x: int):\n    return x"),
        ("int", 'def f(x: "int"):\n    return x'),
    ],
)
def test_module_rename_cannot_capture_builtin_reads(
    tmp_path: Path, name: str, consumer: str
) -> None:
    source = HELPER + consumer + "\n"
    _project(tmp_path, {"main.py": source})
    with pytest.raises(ValueError, match="capture|annotation"):
        plan_renames(tmp_path, [("__extracted_func_0", name, None)])
    assert (tmp_path / "main.py").read_text() == source


def test_module_rename_may_use_unreferenced_builtin_spelling(tmp_path: Path) -> None:
    _project(tmp_path, {"main.py": HELPER + "print(__extracted_func_0(3))\n"})
    before = _run(tmp_path)
    plan, _ = plan_renames(tmp_path, [("__extracted_func_0", "len", None)])
    apply_changes(plan)
    assert _run(tmp_path) == before


@pytest.mark.parametrize(
    "prefix,lookup",
    [
        ("import builtins as bi\n", "bi.globals()"),
        ("from builtins import globals as namespace\n", "namespace()"),
        ("namespace = globals\n", "namespace()"),
    ],
)
def test_aliased_global_reflection_prevents_rename(
    tmp_path: Path, prefix: str, lookup: str
) -> None:
    _project(tmp_path, {"main.py": prefix + HELPER + f'print({lookup}["__extracted_func_0"](3))\n'})
    with pytest.raises(ValueError, match="Dynamic"):
        plan_renames(tmp_path, [("__extracted_func_0", "fresh", None)])


@pytest.mark.parametrize(
    "prefix,lookup",
    [
        ("import builtins as bi\n", "bi.getattr"),
        ("from builtins import getattr as lookup\n", "lookup"),
        ("lookup, unused = getattr, None\n", "lookup"),
    ],
)
def test_aliased_method_reflection_prevents_rename(
    tmp_path: Path, prefix: str, lookup: str
) -> None:
    _project(
        tmp_path,
        {
            "main.py": prefix
            + "class A:\n    def _extracted_func_0(self):\n        return 3\n"
            + f'print({lookup}(A(), "_extracted_func_0")())\n'
        },
    )
    with pytest.raises(ValueError, match="referenced by name"):
        plan_renames(tmp_path, [("_extracted_func_0", "fresh", None)])


@pytest.mark.parametrize("new", ["__len__", "__iter__", "__getattr__"])
def test_method_rename_cannot_introduce_protocol_behavior(tmp_path: Path, new: str) -> None:
    _project(
        tmp_path, {"main.py": "class A:\n    def _extracted_func_0(self):\n        return 3\n"}
    )
    with pytest.raises(ValueError, match="Special method"):
        plan_renames(tmp_path, [("_extracted_func_0", new, None)])


@pytest.mark.parametrize(
    "prefix,lookup",
    [
        ("import builtins as bi\n", "bi.locals()"),
        ("from builtins import locals as namespace\n", "namespace()"),
    ],
)
def test_parameter_rename_refuses_local_namespace_observation(
    tmp_path: Path, prefix: str, lookup: str
) -> None:
    _project(
        tmp_path,
        {
            "main.py": prefix
            + f'def __extracted_func_0(__param_0):\n    return {lookup}["__param_0"]\n'
        },
    )
    with pytest.raises(ValueError, match="Dynamic"):
        plan_renames(tmp_path, [("__extracted_func_0.__param_0", "value", None)])


def test_method_rename_cannot_remove_protocol_behavior(tmp_path: Path) -> None:
    _project(tmp_path, {"main.py": "class A:\n    def __len__(self):\n        return 3\n"})
    with pytest.raises(ValueError, match="Special method"):
        plan_renames(tmp_path, [("__len__", "ordinary", None)])
