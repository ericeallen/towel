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

"""Audit shape 5: reuse requires a clean rebinding scan and strict ownership proof."""

from pathlib import Path

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

PAIR = """def total(value):
    amount = value + 1
    amount = amount * 2
    return amount

def report(value):
    amount = value + 1
    amount = amount * 2
    return amount
"""


def test_clean_relaxed_project_redirects(tmp_path: Path) -> None:
    """Audit shape 5: preserve the user's function instead of creating its twin."""
    path = tmp_path / "mod.py"
    path.write_text(PAIR)
    proposals = UnificationRefactorEngine(min_lines=1, strict=False).analyze_file(str(path))
    assert any(p.reused_function is not None for p in proposals)


@pytest.mark.parametrize(
    "mutation",
    [
        "mod.total = lambda value: 0",
        "del mod.total",
        'mod.__dict__["total"] = lambda value: 0',
        'vars(mod)["total"] = lambda value: 0',
        'del vars(mod)["total"]',
        "vars(mod).update(total=lambda value: 0)",
        "mod.__dict__.clear()",
        'vars(mod).pop("total")',
        'vars(mod).__setitem__("total", lambda value: 0)',
        "vars(mod)[name] = replacement",
        'setattr(mod, "total", lambda value: 0)',
        "setattr(mod, name, replacement)",
        'delattr(mod, "total")',
        'patch("mod.total", replacement)',
        'patch.object(mod, "total", replacement)',
        'monkeypatch.setattr("mod.total", replacement)',
    ],
)
def test_cross_module_rebinding_blocks_redirect(
    tmp_path: Path, mutation: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Audit shape 5: the three-module hazard is visible even outside duplicate sites."""
    (tmp_path / "mod.py").write_text(
        PAIR.replace("def report(value):\n", "def report(value):\n    print(value)\n")
    )
    (tmp_path / "driver.py").write_text("import mod\n" + mutation + "\nmod.report(3)\n")
    (tmp_path / "consumer.py").write_text("import driver\n")
    proposals = UnificationRefactorEngine(min_lines=1, strict=False).analyze_directory(
        str(tmp_path)
    )
    assert proposals
    assert all(p.reused_function is None for p in proposals)
    assert any(
        f"{tmp_path / 'driver.py'}:" in record.message and "may rebind" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize(
    "mutation",
    [
        'globals()["total"] = lambda value: 0',
        'del globals()["total"]',
    ],
)
def test_provider_namespace_mapping_blocks_redirect(
    tmp_path: Path, mutation: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Audit shape 5: namespace dictionaries can replace the provider binding."""
    (tmp_path / "mod.py").write_text(
        PAIR.replace("def report(value):\n", "def report(value):\n    print(value)\n")
        + mutation
        + "\n"
    )
    (tmp_path / "driver.py").write_text("import mod\nmod.report(3)\n")
    (tmp_path / "consumer.py").write_text("import driver\n")
    proposals = UnificationRefactorEngine(min_lines=1, strict=False).analyze_directory(
        str(tmp_path)
    )
    assert proposals
    assert all(p.reused_function is None for p in proposals)
    assert any(
        f"{tmp_path / 'mod.py'}:" in record.message and "may rebind" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("complete", [False, True])
def test_strict_redirect_requires_complete_scope(tmp_path: Path, complete: bool) -> None:
    """Audit shape 5: strict redirects only a caller with no ownership transfer."""
    source = """def total(value):
    print(value)
    print(value + 1)
    print(value + 2)

def report(value):
    print("before")
    print(value)
    print(value + 1)
    print(value + 2)
    print("after", value)
"""
    path = tmp_path / "mod.py"
    path.write_text(source)
    proposals = UnificationRefactorEngine(
        min_lines=1, strict=True, complete_scope=complete
    ).analyze_file(str(path))
    assert any(p.reused_function is not None for p in proposals) is complete


def test_strict_whole_function_ownership_declines_redirect(tmp_path: Path) -> None:
    """Audit shape 5: completeness cannot authorize changing a user's signature."""
    path = tmp_path / "mod.py"
    path.write_text(PAIR)
    proposals = UnificationRefactorEngine(
        min_lines=1, strict=True, complete_scope=True
    ).analyze_file(str(path))
    assert all(p.reused_function is None for p in proposals)


@pytest.mark.parametrize(
    "mutation",
    [
        "total = replacement",
        "try:\n    pass\nexcept Exception as total:\n    pass",
        "match value:\n    case total:\n        pass",
        "del total",
        "import other as total",
        "from other import total",
        "def total(value):\n    return 0",
        "def update():\n    global total\n    total = replacement",
        "def update():\n    global total\n    del total",
        "def update(value=(total := replacement)):\n    pass",
    ],
)
def test_module_rebinding_is_located(tmp_path: Path, mutation: str) -> None:
    """Audit shape 5: every module-level binding mutation includes a source location."""
    import ast
    from towel.unification.rebinding import rebinding_reason

    path = str(tmp_path / "mod.py")
    source = PAIR + mutation + "\n"
    target = ast.parse(source).body[0]
    assert isinstance(target, ast.FunctionDef)
    reason = rebinding_reason({path: source}, path, target)
    assert reason is not None
    assert f"{path}:" in reason and "may rebind" in reason


def test_inferred_scope_reads_consumers_outside_selection(tmp_path: Path) -> None:
    """Audit shape 5: project scope includes a rebinder with no duplicate code."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    path = tmp_path / "mod.py"
    path.write_text(PAIR.replace("def report(value):\n", "def report(value):\n    print(value)\n"))
    (tmp_path / "consumer.py").write_text("import mod\nmod.total = lambda value: 0\n")
    proposals = UnificationRefactorEngine(min_lines=1, strict=False).analyze_file(str(path))
    assert proposals and all(p.reused_function is None for p in proposals)


@pytest.mark.parametrize("command", ["dry", "preview"])
def test_complete_scope_option_reaches_engine_options(command: str) -> None:
    """Audit shape 5: both public entry points carry the completeness declaration."""
    from towel.cli import DryOptions, PreviewOptions, _build_parser

    positional = ["input.py", "output.py"] if command == "dry" else ["input.py"]
    args = _build_parser().parse_args([command, *positional, "--complete-scope"])
    options = (
        DryOptions.from_namespace(args)
        if command == "dry"
        else PreviewOptions.from_namespace(args).dry_options("output.py")
    )
    assert options.complete_scope


def test_redirect_keeps_existing_function_comments(tmp_path: Path) -> None:
    """Audit shape 5: reusing a user's function keeps its source and comments intact."""
    import ast

    original = PAIR.replace(
        "    amount = value + 1", "    amount = value + 1  # original calculation", 1
    )
    path = tmp_path / "mod.py"
    path.write_text(original)
    engine = UnificationRefactorEngine(min_lines=1, strict=False)
    proposal = next(p for p in engine.analyze_file(str(path)) if p.reused_function is not None)
    changed = engine.apply_refactoring(str(path), proposal)
    before, after = ast.parse(original), ast.parse(changed)
    assert ast.dump(before.body[0]) == ast.dump(after.body[0])
    assert "# original calculation" in changed
    assert "__extracted_func" not in changed
    namespace: dict[str, object] = {}
    exec(changed, namespace)
    report = namespace["report"]
    assert callable(report) and report(4) == 10


def test_redirect_uses_an_existing_import_of_the_provider(tmp_path: Path) -> None:
    """Audit shape 5: a caller already importing the exact provider can reach it."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    provider, borrower = PAIR.split("\ndef report(value):\n")
    (package / "mod.py").write_text(provider)
    (package / "report.py").write_text(
        "from pkg.mod import total\n\ndef report(value):\n    print(value)\n" + borrower
    )
    engine = UnificationRefactorEngine(min_lines=1, strict=False, cross_module_helpers=True)
    proposals = engine.analyze_directory(str(tmp_path))
    assert any(p.reused_function is not None for p in proposals)


def test_redirect_never_drops_an_independently_rebound_ambient_argument(tmp_path: Path) -> None:
    """Audit shape 5: equal imports do not make two modules' bindings interchangeable."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="pkg"\nversion="0"\n')
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "ops.py").write_text("def transform(value):\n    return value * 2\n")
    body = "    amount = transform(value)\n    amount = amount + 1\n    amount = amount * 2\n    return amount\n"
    (package / "mod.py").write_text(
        "def total(value):\n" + body + "\nfrom pkg.ops import transform\n"
    )
    (package / "other.py").write_text(
        "from pkg.ops import transform\nimport pkg.mod\n\ndef report(value):\n    print(value)\n"
        + body
    )
    (package / "driver.py").write_text(
        "import pkg.other\npkg.other.transform = lambda value: value * 3\n"
    )
    engine = UnificationRefactorEngine(min_lines=1, strict=False, cross_module_helpers=True)
    proposals = engine.analyze_directory(str(tmp_path))
    assert proposals and all(p.reused_function is None for p in proposals)


def test_redirect_does_not_invent_a_return_value_for_an_implicit_none_function(
    tmp_path: Path,
) -> None:
    """Audit shape 5: retained locals do not make an original None-returning function return them."""
    source = """def total():
    a = object()
    b = object()
    print(a, b)

def report():
    a = object()
    b = object()
    print(a, b)
"""
    path = tmp_path / "mod.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=1, strict=True, complete_scope=True, skip_trivial_helpers=False
    )
    proposals = engine.analyze_file(str(path))
    assert proposals and all(p.reused_function is None for p in proposals)


def test_unreadable_consumer_declines_redirect_without_losing_extraction(tmp_path: Path) -> None:
    """Audit shape 5: incomplete read-only scope blocks reuse, not an ordinary extraction."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    path = tmp_path / "mod.py"
    path.write_text(PAIR)
    (tmp_path / "consumer.py").write_bytes(b"\xff\xff\n")
    proposals = UnificationRefactorEngine(min_lines=1, strict=False).analyze_file(str(path))
    assert proposals and all(p.reused_function is None for p in proposals)
