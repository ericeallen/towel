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

"""Review item 5: relaxed mode preserves captured builtin mappings and lookup effects."""

from pathlib import Path

import pytest

from tests.test_builtin_mapping_operations import _module
from tests.test_definition_time_builtins import _source, WILDCARD_REPLACEMENT
from tests.test_parameterize_builtins import _run, _write
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.relaxed_mode


def _preserved(root: Path, target: str, driver: str, *, flag: bool) -> None:
    before = _run(root, driver)
    engine = UnificationRefactorEngine(
        strict=False,
        min_lines=3,
        max_parameters=6,
        parameterize_builtins=flag,
        skip_trivial_helpers=False,
    )
    final, applied, _ = engine.refactor_to_fixed_point(str(root / target), progress="none")
    assert applied > 0, engine.declined_pairs
    (root / target).write_text(final)
    assert _run(root, driver) == before


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    "body,first,second,prefix",
    [
        ("x = custom(data)", "lambda data: 100", "lambda data: 200", ""),
        (
            "values = [custom(item) for item in data]\nx = values[0]",
            "lambda item: 100",
            "lambda item: 200",
            "",
        ),
        ("x = (lambda value=custom(data): value)()", "lambda data: 100", "lambda data: 200", ""),
        (
            "values = [item for item in custom(data)]\nx = values[0]",
            "lambda data: [100]",
            "lambda data: [200]",
            "",
        ),
        (
            "x = 0\nfor item in (value for value in custom(data)):\n    x += item",
            "lambda data: [100]",
            "lambda data: [200]",
            "",
        ),
        (
            "values = [1 for custom[0] in data]\nx = values[0]",
            "first_holder",
            "second_holder",
            "first_holder = [0]\nsecond_holder = [0]\n",
        ),
    ],
)
def test_captured_custom_mapping(
    tmp_path: Path, flag: bool, body: str, first: str, second: str, prefix: str
) -> None:
    source = _module(body, '{"custom": ' + first + "}", '{"custom": ' + second + "}", prefix)
    _write(tmp_path, {"sample.py": source})
    _preserved(tmp_path, "sample.py", "sample.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    "body", ["import provider\nx = provider.value", "from provider import value\nx = value"]
)
def test_captured_import_hook(tmp_path: Path, flag: bool, body: str) -> None:
    prefix = "class FirstProvider:\n    value = 100\nclass SecondProvider:\n    value = 200\ndef first_import(*args, **kwargs):\n    return FirstProvider\ndef second_import(*args, **kwargs):\n    return SecondProvider\n"
    source = _module(body, '{"__import__": first_import}', '{"__import__": second_import}', prefix)
    _write(tmp_path, {"sample.py": source})
    _preserved(tmp_path, "sample.py", "sample.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
def test_captured_class_spelling(tmp_path: Path, flag: bool) -> None:
    source = _module(
        "x = __class__(data)", '{"__class__": lambda data: 100}', '{"__class__": lambda data: 200}'
    )
    _write(tmp_path, {"sample.py": source})
    _preserved(tmp_path, "sample.py", "sample.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("position", ["before", "between", "after"])
@pytest.mark.parametrize(
    "replacement",
    [
        '__builtins__ = {"len": lambda data: 100}\n',
        'if True:\n    __builtins__ = {"len": lambda data: 100}\n',
        'mapping = {"len": lambda data: 100}\n__builtins__ = mapping\n',
        'def replace():\n    global __builtins__\n    __builtins__ = {"len": lambda data: 100}\nreplace()\n',
        "from replacement import *\n",
    ],
)
def test_mapping_replacement_positions(
    tmp_path: Path, flag: bool, position: str, replacement: str
) -> None:
    _write(
        tmp_path,
        {"m.py": _source(**{position: replacement}), "replacement.py": WILDCARD_REPLACEMENT},
    )
    _preserved(tmp_path, "m.py", "m.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("position", ["before", "between", "after"])
def test_custom_name_mapping_replacement(tmp_path: Path, flag: bool, position: str) -> None:
    replacements = {position: '__builtins__ = {"custom": lambda data: 200}\n'}
    replacements["before"] = '__builtins__ = {"custom": lambda data: 100}\n' + replacements.get(
        "before", ""
    )
    _write(tmp_path, {"m.py": _source(**replacements).replace("len(data)", "custom(data)")})
    _preserved(tmp_path, "m.py", "m.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("parameterized", [False, True])
def test_circular_mapping_writer(tmp_path: Path, flag: bool, parameterized: bool) -> None:
    replacement = "import target\n" + (
        'def configure(receiver):\n    receiver.__builtins__ = {"custom": lambda data: 100}\nconfigure(target)\n'
        if parameterized
        else 'target.__builtins__ = {"custom": lambda data: 100}\n'
    )
    _write(
        tmp_path,
        {
            "target.py": _source(before="import replacement\n").replace(
                "len(data)", "custom(data)"
            ),
            "replacement.py": replacement,
            "driver.py": "import target\n",
        },
    )
    _preserved(tmp_path, "target.py", "driver.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
def test_deleted_global_falls_back_to_captured_mapping(tmp_path: Path, flag: bool) -> None:
    source = (
        '__builtins__ = {"custom": lambda data: 100}\ncustom = lambda data: 7\n'
        + _source(between='__builtins__ = {"custom": lambda data: 200}\n', after="del custom\n")
    ).replace("len(data)", "custom(data)")
    _write(tmp_path, {"m.py": source})
    _preserved(tmp_path, "m.py", "m.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
def test_methods_capture_different_mappings(tmp_path: Path, flag: bool) -> None:
    from tests.test_definition_time_builtins import BODY

    body = "\n".join("    " + line for line in BODY.splitlines())
    source = (
        "class First:\n    def first(self, data):\n"
        + body
        + '\n\n__builtins__ = {"len": lambda data: 100, "__build_class__": __build_class__}\n\nclass Second:\n    def second(self, data):\n'
        + body
        + "\nprint(First().first([1]), Second().second([1]))\n"
    )
    _write(tmp_path, {"m.py": source})
    _preserved(tmp_path, "m.py", "m.py", flag=flag)


@pytest.mark.parametrize("flag", [False, True])
def test_cross_module_mapping_is_preserved(tmp_path: Path, flag: bool) -> None:
    from tests.test_definition_time_builtins import BODY

    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="pkg"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\ndef first(data):\n" + BODY,
            "pkg/b.py": 'def unrelated():\n    pass\n__builtins__ = {"len": lambda data: 100}\ndef second(data):\n'
            + BODY,
            "drive.py": "from pkg.a import first\nfrom pkg.b import second\nprint(first([1]), second([1]))\n",
        },
    )
    before = _run(tmp_path, "drive.py")
    engine = UnificationRefactorEngine(
        strict=False,
        skip_trivial_helpers=False,
        cross_module_helpers=True,
        parameterize_builtins=flag,
    )
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path), str(tmp_path), progress="none"
    )
    assert sum(count for count, _ in results.values()) > 0, engine.declined_pairs
    assert _run(tmp_path, "drive.py") == before


@pytest.mark.parametrize("change", ["writer", "project-marker", "second-project"])
def test_changed_project_facts_preserve_captured_lookup(tmp_path: Path, change: str) -> None:
    from tests.test_definition_time_builtins import BODY

    root = tmp_path / "inner"
    root.mkdir()
    source = (
        "import writer\n\ndef first(data):\n"
        + BODY
        + "\ndef second(data):\n"
        + BODY
        + "\ndef third(data):\n"
        + BODY
        + "\nprint(first([1]), second([2]), third([3]))\n"
    )
    _write(root, {"target.py": source, "writer.py": "VALUE = 1\n", "driver.py": "import target\n"})
    target = root / "target.py"
    engine = UnificationRefactorEngine(
        strict=False,
        min_lines=3,
        max_parameters=6,
        parameterize_builtins=True,
        skip_trivial_helpers=False,
    )
    assert engine.analyze_file(str(target))
    if change == "project-marker":
        (tmp_path / "pyproject.toml").write_text('[project]\nname="sample"\nversion="0"\n')
    writer = root / "writer.py"
    writer.write_text('import target\ntarget.__builtins__ = {"len": lambda data: 100}\n')
    if change == "second-project":
        other = tmp_path / "other"
        _write(other, {"target.py": source})
        engine.analyze_files([str(other / "target.py")])
    before = _run(root, "driver.py")
    assert before == "306 306 306\n"
    proposals = engine.analyze_files([str(target)], changed_files=frozenset({str(writer)}))
    assert proposals, "The independent arithmetic suffix must still exercise extraction"
    for proposal in proposals:
        target.write_text(engine.apply_refactoring(str(target), proposal))
        assert _run(root, "driver.py") == before
        target.write_text(source)
