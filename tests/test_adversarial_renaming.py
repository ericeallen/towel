"""Execute renamed consumers; compilation alone cannot establish binding correctness."""

from pathlib import Path
import subprocess
import sys

import pytest

from towel.cli import _apply_rename_mappings, _rename_function_in_directory

HELPER = "def __extracted_func_0():\n    return 7\n"


def execute(path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_fstrings_unicode_columns_comments_and_unrelated_attributes(tmp_path, newline):
    source = (
        "from types import SimpleNamespace\n"
        + HELPER
        + "# __extracted_func_0() remains a comment\n"
        + 'label = "__extracted_func_0()"\n'
        + 'obj = SimpleNamespace(**{"__extracted_func_0": 11})\n'
        + 'print("π", f"{__extracted_func_0()}", obj.__extracted_func_0, label)\n'
    ).replace("\n", newline)
    path = tmp_path / "main.py"
    path.write_bytes(source.encode())
    expected = execute(path)
    _rename_function_in_directory(tmp_path, "__extracted_func_0", "answer", False)
    assert execute(path) == expected
    result = path.read_bytes()
    assert b"obj.__extracted_func_0" in result
    assert b'"__extracted_func_0()"' in result
    assert b"# __extracted_func_0() remains a comment" in result
    assert b"{answer()}" in result
    if newline == "\r\n":
        assert result.count(b"\n") == result.count(b"\r\n")


def test_shadowing_lambda_comprehension_defaults_and_nested_closure(tmp_path):
    path = tmp_path / "main.py"
    path.write_text(HELPER.replace("__extracted_func_0", "_extracted_func_0") + """
def outer(_extracted_func_0):
    def nested():
        return _extracted_func_0()
    return nested()

def shadow(_extracted_func_0=_extracted_func_0):
    return _extracted_func_0()

class Container:
    def method(self):
        return _extracted_func_0()

print(outer(lambda: 11), shadow(), Container().method())
print((lambda _extracted_func_0: _extracted_func_0())(lambda: 13))
print([_extracted_func_0() for _extracted_func_0 in [lambda: 17]])
print([_extracted_func_0() for item in [1]])
""")
    expected = execute(path)
    _rename_function_in_directory(tmp_path, "_extracted_func_0", "answer", False)
    assert execute(path) == expected
    assert "def shadow(_extracted_func_0=answer)" in path.read_text()
    assert "return _extracted_func_0()" in path.read_text()


def test_file_qualified_imports_aliases_relative_imports_and_reexports(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(HELPER)
    (package / "b.py").write_text(
        "from .a import __extracted_func_0\n"
        "from .a import __extracted_func_0 as bound\n"
        "from . import a as module\n"
        "def check():\n    return __extracted_func_0(), bound(), module.__extracted_func_0()\n"
    )
    main = tmp_path / "main.py"
    main.write_text(
        "import pkg.a\nimport pkg.a as alias\n"
        "from pkg.b import check, __extracted_func_0\n"
        "print(check(), pkg.a.__extracted_func_0(), alias.__extracted_func_0(), __extracted_func_0())\n"
    )
    expected = execute(main)
    _apply_rename_mappings(tmp_path, {"pkg/a.py:__extracted_func_0": "answer"}, False)
    assert execute(main) == expected
    # A generated, unaliased binding follows the rename through every re-export;
    # a user alias keeps its own name.
    assert "from .a import answer\n" in (package / "b.py").read_text()
    assert "from .a import answer as bound" in (package / "b.py").read_text()
    assert "return answer(), bound(), module.answer()" in (package / "b.py").read_text()
    assert "from pkg.b import check, answer" in main.read_text()
    assert "pkg.a.answer()" in main.read_text() and "__extracted_func_0" not in main.read_text()


def test_explicit_global_binding_is_renamed(tmp_path):
    path = tmp_path / "main.py"
    path.write_text(HELPER + """
def replace():
    global __extracted_func_0
    __extracted_func_0 = lambda: 19
replace()
print(__extracted_func_0())
""")
    expected = execute(path)
    _rename_function_in_directory(tmp_path, "__extracted_func_0", "answer", False)
    assert execute(path) == expected
    assert "global answer" in path.read_text()


@pytest.mark.parametrize(
    "suffix, message",
    [
        ("def use(answer):\n    return __extracted_func_0()\n", "capture"),
        ('print(globals()["__extracted_func_0"]())\n', "Dynamic"),
        ('__all__ = ["__extracted_func_0"]\n', "exports"),
        ("class C:\n    __extracted_func_0 = __extracted_func_0\n", "class namespace"),
        ("class __extracted_func_0:\n    pass\n", "Non-function binding"),
    ],
)
def test_unsupported_binding_cases_fail_before_any_write(tmp_path, suffix, message):
    first = tmp_path / "a.py"
    last = tmp_path / "z.py"
    first.write_text(HELPER)
    last.write_text(HELPER + suffix)
    originals = {path: path.read_bytes() for path in (first, last)}
    with pytest.raises(ValueError, match=message):
        _rename_function_in_directory(tmp_path, "__extracted_func_0", "answer", False)
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize(
    "consumer, message",
    [
        ("import a\nother = a\nprint(other.__extracted_func_0())\n", "escapes"),
        ("from a import *\n", "Star import"),
        ("import a\na = object()\n", "Rebound|escapes"),
    ],
)
def test_unresolved_module_consumers_are_rejected(tmp_path, consumer, message):
    (tmp_path / "a.py").write_text(HELPER)
    (tmp_path / "b.py").write_text(consumer)
    before = {path: path.read_bytes() for path in tmp_path.glob("*.py")}
    with pytest.raises(ValueError, match=message):
        _apply_rename_mappings(tmp_path, {"a.py:__extracted_func_0": "answer"}, False)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize(
    "source, message",
    [
        ("class C:\n    " + HELPER.replace("\n", "\n    "), "mangling"),
        ("def outer():\n    " + HELPER.replace("\n", "\n    "), "Nested helper"),
    ],
)
def test_nested_and_private_class_helpers_fail_visibly(tmp_path, source, message):
    path = tmp_path / "main.py"
    path.write_text(source)
    with pytest.raises(ValueError, match=message):
        _rename_function_in_directory(tmp_path, "__extracted_func_0", "answer", False)
    assert path.read_text() == source


@pytest.mark.parametrize(
    "consumer",
    [
        "def check(a):\n    before = a.__extracted_func_0()\n    import a\n    return before\n",
        "if condition:\n    import a as chosen\nelse:\n    import other as chosen\nprint(chosen.__extracted_func_0())\n",
        "class Container:\n    import a\nprint(Container.a.__extracted_func_0())\n",
    ],
)
def test_ambiguous_import_bindings_are_rejected(tmp_path, consumer):
    (tmp_path / "a.py").write_text(HELPER)
    (tmp_path / "b.py").write_text(consumer)
    before = {path: path.read_bytes() for path in tmp_path.glob("*.py")}
    with pytest.raises(ValueError, match="alias"):
        _apply_rename_mappings(tmp_path, {"a.py:__extracted_func_0": "answer"}, False)
    assert {path: path.read_bytes() for path in before} == before


def test_many_to_one_mappings_fail_before_write(tmp_path):
    path = tmp_path / "main.py"
    original = HELPER + HELPER.replace("__extracted_func_0", "__extracted_func_1")
    path.write_text(original)
    with pytest.raises(ValueError, match="same new name"):
        _apply_rename_mappings(
            tmp_path, {"__extracted_func_0": "answer", "__extracted_func_1": "answer"}, False
        )
    assert path.read_text() == original


@pytest.mark.parametrize(
    "suffix",
    [
        'def use(x: "__extracted_func_0"):\n    return x\n',
        'lookup = globals\nprint(lookup()["__extracted_func_0"]())\n',
        "_C__extracted_func_0 = lambda: 11\nclass C:\n    def call(self):\n        return __extracted_func_0()\nprint(C().call())\n",
    ],
)
def test_implicit_name_lookup_boundaries_fail_visibly(tmp_path, suffix):
    path = tmp_path / "main.py"
    original = HELPER + suffix
    path.write_text(original)
    execute(path)
    with pytest.raises(ValueError, match="annotation|namespace|mangling"):
        _rename_function_in_directory(tmp_path, "__extracted_func_0", "answer", False)
    assert path.read_text() == original


@pytest.mark.parametrize("configuration", ["ambient_git", "setuptools_src", "package_target"])
def test_import_identity_uses_python_layout_not_ancestor_git(tmp_path, configuration):
    # A Git marker above the explicit source target is unrelated to sys.path.
    (tmp_path / ".git").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    source_root = project / "src" if configuration == "setuptools_src" else project
    source_root.mkdir(exist_ok=True)
    if configuration == "setuptools_src":
        (project / "pyproject.toml").write_text('[tool.setuptools.package-dir]\n"" = "src"\n')
    package = source_root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    helper = package / "helpers.py"
    helper.write_text(HELPER)
    consumer = package / "consumer.py"
    consumer.write_text(
        "from . import helpers\ndef check():\n    return helpers.__extracted_func_0()\n"
    )
    main = source_root / "main.py"
    main.write_text("from pkg.consumer import check\nprint(check())\n")
    expected = execute(main)
    target = package if configuration == "package_target" else project
    relative = helper.relative_to(target)
    _apply_rename_mappings(target, {f"{relative}:__extracted_func_0": "answer"}, False)
    assert execute(main) == expected
    assert "helpers.answer()" in consumer.read_text()
