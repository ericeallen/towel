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

"""Helpers preserve the builtins mapping captured when each caller was created."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_parameterize_builtins import _helpers, _refactor, _run, _write
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import builtin_mapping_reads

BODY = """\
    x = len(data)
    y = x + 2
    z = y * 3
    return z
"""

WILDCARD_REPLACEMENT = '__all__ = ["__builtins__"]\n__builtins__ = {"len": lambda data: 100}\n'


def _source(before: str = "", between: str = "", after: str = "") -> str:
    return (
        "def unrelated():\n    pass\n\n"
        + before
        + "\ndef first(data):\n"
        + BODY
        + between
        + "\ndef second(data):\n"
        + BODY
        + after
        + "\nprint(first([1]), second([1]))\n"
    )


def _assert_original_blocks_have_no_mapping_reads(source: str) -> None:
    module = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    functions = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name in {"first", "second"}
    ]
    assert len(functions) == 2
    for function in functions:
        assert builtin_mapping_reads(function, function.body, analyzer, source) == frozenset()


@pytest.mark.parametrize("flag", [False, True])
def test_replacement_after_an_earlier_definition_preserves_caller_mapping(
    tmp_path: Path, flag: bool
) -> None:
    source = _source(before='__builtins__ = {"len": lambda data: 100}\n')
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    assert expected == "306 306\n"
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert _run(tmp_path, "m.py") == expected
    assert "builtin_mapping_replaced" in declined
    assert not any(
        isinstance(node, ast.Name) and node.id == "len"
        for helper in _helpers(tmp_path)
        for node in ast.walk(helper)
    )


@pytest.mark.parametrize("flag", [False, True])
def test_replacement_between_definitions_preserves_distinct_mappings(
    tmp_path: Path, flag: bool
) -> None:
    source = _source(between='__builtins__ = {"len": lambda data: 100}\n')
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    assert expected == "9 306\n"
    _refactor(tmp_path / "m.py", flag)
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    ("before", "between", "after", "prefix", "expected"),
    [
        ("", "", '__builtins__ = {"len": lambda data: 100}\n', "", "9 9\n"),
        ("", "", "", '__builtins__ = {"len": lambda data: 100}\n', "306 306\n"),
        ('if True:\n    __builtins__ = {"len": lambda data: 100}\n', "", "", "", "306 306\n"),
        ('mapping = {"len": lambda data: 100}\n__builtins__ = mapping\n', "", "", "", "306 306\n"),
        (
            'def replace():\n    global __builtins__\n    __builtins__ = {"len": lambda data: 100}\nreplace()\n',
            "",
            "",
            "",
            "306 306\n",
        ),
    ],
    ids=["after", "before-all", "conditional", "aliased", "global"],
)
def test_ordinary_mapping_replacements_are_conservatively_declined(
    tmp_path: Path,
    flag: bool,
    before: str,
    between: str,
    after: str,
    prefix: str,
    expected: str,
) -> None:
    _write(tmp_path, {"m.py": prefix + _source(before, between, after)})
    assert _run(tmp_path, "m.py") == expected
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert "builtin_mapping_replaced" in declined
    assert _run(tmp_path, "m.py") == expected
    # Fixed-point reparsing must retain the guard on each pass.
    final = (tmp_path / "m.py").read_bytes()
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied == 0
    assert (tmp_path / "m.py").read_bytes() == final


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    ("position", "expected"),
    [("before", "306 306\n"), ("between", "9 306\n"), ("after", "9 9\n")],
)
def test_explicit_wildcard_export_preserves_definition_time_builtin_mappings(
    tmp_path: Path, flag: bool, position: str, expected: str
) -> None:
    source = _source(**{position: "from replacement import *\n"})
    _write(tmp_path, {"replacement.py": WILDCARD_REPLACEMENT, "m.py": source})
    assert _run(tmp_path, "m.py") == expected
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert _run(tmp_path, "m.py") == expected
    assert "builtin_mapping_replaced" in declined
    final = (tmp_path / "m.py").read_bytes()
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied == 0
    assert (tmp_path / "m.py").read_bytes() == final


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("local_builtin", [False, True])
def test_wildcard_mapping_replacement_keeps_nonbuiltin_extraction_useful(
    tmp_path: Path, flag: bool, local_builtin: bool
) -> None:
    source = _source(before="from replacement import *\n")
    if local_builtin:
        source = source.replace("(data):", "(data, len):").replace(
            "first([1]), second([1])", "first([1], lambda data: 7), second([1], lambda data: 7)"
        )
    else:
        source = source.replace("len(data)", "data[0]")
    _assert_original_blocks_have_no_mapping_reads(source)
    _write(tmp_path, {"replacement.py": WILDCARD_REPLACEMENT, "m.py": source})
    expected = _run(tmp_path, "m.py")
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied > 0
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    ("position", "expected"),
    [("before", "606 606\n"), ("between", "306 606\n"), ("after", "306 306\n")],
)
def test_custom_builtin_names_preserve_each_functions_captured_mapping(
    tmp_path: Path, flag: bool, position: str, expected: str
) -> None:
    replacements = {position: '__builtins__ = {"custom": lambda data: 200}\n'}
    replacements["before"] = '__builtins__ = {"custom": lambda data: 100}\n' + replacements.get(
        "before", ""
    )
    source = _source(**replacements).replace("len(data)", "custom(data)")
    _write(tmp_path, {"m.py": source})
    assert _run(tmp_path, "m.py") == expected
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert _run(tmp_path, "m.py") == expected
    assert "builtin_mapping_replaced" in declined
    final = (tmp_path / "m.py").read_bytes()
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied == 0
    assert (tmp_path / "m.py").read_bytes() == final


@pytest.mark.parametrize("flag", [False, True])
def test_deleted_module_global_falls_back_to_each_captured_custom_builtin(
    tmp_path: Path, flag: bool
) -> None:
    source = (
        '__builtins__ = {"custom": lambda data: 100}\ncustom = lambda data: 7\n'
        + _source(between='__builtins__ = {"custom": lambda data: 200}\n', after="del custom\n")
    ).replace("len(data)", "custom(data)")
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    assert expected == "306 606\n"
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert _run(tmp_path, "m.py") == expected
    assert "builtin_mapping_replaced" in declined


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("closure", [False, True], ids=["local-parameter", "closure"])
def test_custom_builtin_spelling_keeps_genuinely_lexical_extraction_useful(
    tmp_path: Path, flag: bool, closure: bool
) -> None:
    if closure:
        source = (
            '__builtins__ = {"custom": lambda data: 100}\n'
            + "def outer(custom):\n    def first(data):\n"
            + "\n".join("    " + line for line in BODY.splitlines())
            + "\n    def second(data):\n"
            + "\n".join("    " + line for line in BODY.splitlines())
            + "\n    return first([1]), second([1])\n"
            + "print(outer(lambda data: 7))\n"
        )
        expected = "(27, 27)\n"
    else:
        source = _source(before='__builtins__ = {"custom": lambda data: 100}\n')
        source = source.replace("(data):", "(data, custom):").replace(
            "first([1]), second([1])", "first([1], lambda data: 7), second([1], lambda data: 7)"
        )
        expected = "27 27\n"
    source = source.replace("len(data)", "custom(data)")
    _assert_original_blocks_have_no_mapping_reads(source)
    _write(tmp_path, {"m.py": source})
    assert _run(tmp_path, "m.py") == expected
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied > 0
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("mutation", [False, True])
def test_shared_builtin_mapping_mutation_preserves_useful_extraction(
    tmp_path: Path, flag: bool, mutation: bool
) -> None:
    after = "import builtins\nbuiltins.len = lambda data: 100\n" if mutation else ""
    _write(tmp_path, {"m.py": _source(after=after)})
    expected = _run(tmp_path, "m.py")
    assert expected == ("306 306\n" if mutation else "9 9\n")
    applied, declined = _refactor(tmp_path / "m.py", flag)
    assert applied > 0
    assert "builtin_mapping_replaced" not in declined
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("local_builtin", [False, True])
def test_no_builtin_reads_and_local_shadowing_keep_useful_extraction(
    tmp_path: Path, flag: bool, local_builtin: bool
) -> None:
    source = _source(before='__builtins__ = {"len": lambda data: 100}\n')
    if local_builtin:
        source = source.replace("(data):", "(data, len):").replace(
            "first([1]), second([1])", "first([1], lambda data: 7), second([1], lambda data: 7)"
        )
    else:
        source = source.replace("len(data)", "data[0]")
    _assert_original_blocks_have_no_mapping_reads(source)
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied > 0
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
def test_methods_created_under_different_mappings_preserve_results(
    tmp_path: Path, flag: bool
) -> None:
    source = (
        "class First:\n    def first(self, data):\n"
        + "\n".join("    " + line for line in BODY.splitlines())
        + '\n\n__builtins__ = {"len": lambda data: 100, "__build_class__": __build_class__}\n'
        + "\nclass Second:\n    def second(self, data):\n"
        + "\n".join("    " + line for line in BODY.splitlines())
        + "\n\nprint(First().first([1]), Second().second([1]))\n"
    )
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    assert expected == "9 306\n"
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert "builtin_mapping_replaced" in declined
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
def test_cross_module_mapping_replacement_is_not_fixed_by_a_lookup_thunk(
    tmp_path: Path, flag: bool
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\ndef first(data):\n" + BODY,
            "pkg/b.py": 'def unrelated():\n    pass\n__builtins__ = {"len": lambda data: 100}\ndef second(data):\n'
            + BODY,
            "drive.py": "from pkg.a import first\nfrom pkg.b import second\nprint(first([1]), second([1]))\n",
        },
    )
    expected = _run(tmp_path, "drive.py")
    assert expected == "9 306\n"
    _, declined = _refactor(tmp_path, flag)
    assert "builtin_mapping_replaced" in declined
    assert _run(tmp_path, "drive.py") == expected


@pytest.mark.parametrize("flag", [False, True])
def test_cross_module_wildcard_mapping_replacement_preserves_results(
    tmp_path: Path, flag: bool
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
            "replacement.py": WILDCARD_REPLACEMENT,
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\ndef first(data):\n" + BODY,
            "pkg/b.py": "def unrelated():\n    pass\nfrom replacement import *\ndef second(data):\n"
            + BODY,
            "drive.py": "from pkg.a import first\nfrom pkg.b import second\nprint(first([1]), second([1]))\n",
        },
    )
    expected = _run(tmp_path, "drive.py")
    assert expected == "9 306\n"
    _, declined = _refactor(tmp_path, flag)
    assert _run(tmp_path, "drive.py") == expected
    assert "builtin_mapping_replaced" in declined


def test_local_builtins_assignment_does_not_disable_ordinary_module_extraction(
    tmp_path: Path,
) -> None:
    source = (
        _source()
        + '\ndef local_only():\n    __builtins__ = {"len": lambda data: 100}\n    return __builtins__\n'
    )
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    applied, declined = _refactor(tmp_path / "m.py", False)
    assert applied > 0
    assert "builtin_mapping_replaced" not in declined
    assert _run(tmp_path, "m.py") == expected


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("wildcard", [False, True], ids=["assignment", "wildcard-export"])
def test_default_typed_formatted_cli_preserves_captured_mappings(
    tmp_path: Path, flag: bool, wildcard: bool
) -> None:
    replacement = (
        "from replacement import *\n" if wildcard else '__builtins__ = {"len": lambda data: 100}\n'
    )
    files = {"m.py": _source(between=replacement)}
    if wildcard:
        files["replacement.py"] = WILDCARD_REPLACEMENT
    _write(tmp_path, files)
    expected = _run(tmp_path, "m.py")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from towel.cli import main; sys.exit(main())",
            "dry",
            "m.py",
            "m.py",
            "--no-interactive",
            "--progress",
            "none",
            *(["--parameterize-builtins"] if flag else []),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _run(tmp_path, "m.py") == expected == "9 306\n"


@pytest.mark.parametrize("flag", [False, True])
def test_builtin_cluster_cannot_join_a_safe_locally_bound_pair(tmp_path: Path, flag: bool) -> None:
    source = (
        'def unrelated():\n    pass\n__builtins__ = {"len": lambda data: 100}\n'
        + "def first(data, len):\n"
        + BODY
        + "def second(data, len):\n"
        + BODY
        + "def third(data):\n"
        + BODY
        + "print(first([1], lambda data: 7), second([1], lambda data: 8), third([1]))\n"
    )
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    assert expected == "27 30 306\n"
    applied, _ = _refactor(tmp_path / "m.py", flag)
    assert applied > 0
    assert _run(tmp_path, "m.py") == expected
    functions = {
        node.name: node
        for node in ast.parse((tmp_path / "m.py").read_text()).body
        if isinstance(node, ast.FunctionDef)
    }
    assert any(
        isinstance(node, ast.Name) and node.id == "len" for node in ast.walk(functions["third"])
    )


@pytest.mark.parametrize("flag", [False, True])
def test_comprehension_target_does_not_hide_the_enclosing_builtin_read(
    tmp_path: Path, flag: bool
) -> None:
    source = _source(before='__builtins__ = {"len": lambda data: 100}\n').replace(
        "    x = len(data)", "    [len for len in ()]\n    x = len(data)"
    )
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    _, declined = _refactor(tmp_path / "m.py", flag)
    assert "builtin_mapping_replaced" in declined
    assert _run(tmp_path, "m.py") == expected == "306 306\n"


def test_lexical_mapping_guard_uses_the_analyzed_module_when_source_is_omitted() -> None:
    module = ast.parse(_source(before='__builtins__ = {"len": lambda data: 100}\n'))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "first"
    )
    assert builtin_mapping_reads(function, function.body, analyzer, "") == frozenset({"len"})


def test_closure_shadowed_builtin_keeps_its_captured_local_value(tmp_path: Path) -> None:
    source = (
        '__builtins__ = {"len": lambda data: 100}\n'
        + "def outer(len):\n    def first(data):\n"
        + "\n".join("    " + line for line in BODY.splitlines())
        + "\n    def second(data):\n"
        + "\n".join("    " + line for line in BODY.splitlines())
        + "\n    return first([1]), second([1])\n"
        + "print(outer(lambda data: 7))\n"
    )
    _write(tmp_path, {"m.py": source})
    expected = _run(tmp_path, "m.py")
    assert expected == "(27, 27)\n"
    applied, declined = _refactor(tmp_path / "m.py", False)
    assert applied > 0
    assert "builtin_mapping_replaced" not in declined
    assert _run(tmp_path, "m.py") == expected
