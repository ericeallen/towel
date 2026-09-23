"""Project-level checker and lifecycle regressions from the 1.732 release audit."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import Iterator

import pytest

from towel import type_inference
from towel.type_inference import (
    CheckFailure,
    CheckSuccess,
    CombinedOracle,
    MypyInferrer,
    PyrightOracle,
    RevealRequest,
    Subtyping,
    TypeOracle,
    type_oracle_for_project,
)
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.fixture(params=["mypy", "pyright", "both"])
def oracle(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    name = request.param
    if name in {"mypy", "both"}:
        pytest.importorskip("mypy")
    if name in {"pyright", "both"}:
        pytest.importorskip("pyright")
    checker: TypeOracle = (
        MypyInferrer()
        if name == "mypy"
        else (
            PyrightOracle()
            if name == "pyright"
            else CombinedOracle(MypyInferrer(), [PyrightOracle()])
        )
    )
    try:
        yield checker
    finally:
        checker.close()


def _package(root: Path) -> Path:
    (root / "pyproject.toml").write_text(
        '[tool.mypy]\nignore_missing_imports = false\n[tool.pyright]\ntypeCheckingMode = "basic"\n'
    )
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    return package


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_pretty_mypy_config_checks_in_memory_lines_without_reading_disk(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\npretty = true\nstrict = true\n")
    path = tmp_path / "example.py"
    source = "value: int = 1\n"
    path.write_text(source)
    checker = MypyInferrer()
    try:
        assert checker.is_subtype(str(path), source, [("int", "str"), ("int", "object")]) == [
            Subtyping.NO,
            Subtyping.YES,
        ]
        revealed = checker.reveal([RevealRequest(str(path), source, 2, "", ("value",))])
        assert revealed[(str(path), 2, 0)].removeprefix("builtins.") == "int"
        result = checker.check(str(path), source + "\ndef untyped(value):\n    return value\n")
        assert isinstance(result, CheckSuccess)
        assert any("missing a type annotation" in error.message for error in result.errors)
        assert path.read_text() == source
    finally:
        checker.close()


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
@pytest.mark.parametrize(
    "config_name, config_text",
    [
        (
            "pyrightconfig.json",
            '{"include": ["src"], "exclude": ["src/excluded.py"], "typeCheckingMode": "strict"}',
        ),
        (
            "pyproject.toml",
            '[tool.pyright]\ninclude = ["src"]\nexclude = ["src/excluded.py"]\n'
            'typeCheckingMode = "strict"\n',
        ),
    ],
)
def test_pyright_project_scope_matches_configuration_and_checks_unchanged_consumers(
    tmp_path: Path, config_name: str, config_text: str
) -> None:
    import json

    (tmp_path / config_name).write_text(config_text)
    source = tmp_path / "src"
    source.mkdir()
    host, consumer = source / "host.py", source / "consumer.py"
    host.write_text("def value() -> str:\n    return 'one'\n")
    consumer.write_text("from host import value\ntext: str = value()\n")
    (source / "excluded.py").write_text("value: int = 'excluded'\n")
    (tmp_path / "outside.py").write_text("value: int = 'outside'\n")
    independent = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pyright",
            "--outputjson",
            "--project",
            str(tmp_path),
            "--pythonpath",
            sys.executable,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert independent.returncode == 0, independent.stdout + independent.stderr
    assert json.loads(independent.stdout)["summary"]["filesAnalyzed"] == 2
    checker = PyrightOracle()
    assert checker.check(str(host), host.read_text()) == CheckSuccess()
    changed = checker.check(str(host), "def value() -> int:\n    return 1\n")
    assert isinstance(changed, CheckSuccess)
    assert {error.path for error in changed.errors} == {str(consumer)}
    assert all("reportAssignmentType" in error.message for error in changed.errors)


def test_all_prospective_modules_are_visible_together(tmp_path: Path, oracle: TypeOracle) -> None:
    package = _package(tmp_path)
    host, caller = package / "host.py", package / "caller.py"
    host.write_text("")
    caller.write_text("def run(value: int) -> int:\n    return value + 1\n")
    assert oracle.check_project({str(caller): caller.read_text()}) == CheckSuccess()
    replacements = {
        str(host): "def helper(value: int) -> int:\n    return value + 1\n",
        str(
            caller
        ): "from .host import helper\n\ndef run(value: int) -> int:\n    return helper(value)\n",
    }
    assert oracle.check_project(replacements) == CheckSuccess()
    assert host.read_text() == "", "verification must not change project files"
    assert "import" not in caller.read_text()


def test_new_errors_in_unchanged_consumers_are_detected(tmp_path: Path, oracle: TypeOracle) -> None:
    package = _package(tmp_path)
    host, consumer = package / "host.py", package / "consumer.py"
    host.write_text("def value() -> str:\n    return 'one'\n")
    consumer.write_text("from .host import value\ntext: str = value()\n")
    assert oracle.check(str(host), host.read_text()) == CheckSuccess()
    result = oracle.check(str(host), "def value() -> int:\n    return 1\n")
    assert isinstance(result, CheckSuccess)
    assert any(error.path == str(consumer) for error in result.errors), result


def test_cross_file_extraction_keeps_verified_annotations(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    package = _package(tmp_path)
    for name, multiplier in (("first", 2), ("second", 3)):
        (package / f"{name}.py").write_text(
            f"def {name}(value: int) -> int:\n"
            "    total = value + 1\n    print(total)\n"
            f"    return total * {multiplier}\n"
        )
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle, cross_module_helpers=True
    )
    proposals = engine.analyze_directory(str(package), progress="none")
    assert proposals
    sources = engine.apply_refactoring_multi_file(proposals[0])
    helpers = [
        node
        for source in sources.values()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert helpers and all(helper.returns is not None for helper in helpers)
    assert all(arg.annotation is not None for helper in helpers for arg in helper.args.args)
    assert oracle.check_project(sources) == CheckSuccess()


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_project_any_rule_triggers_annotation_fallback(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\ndisallow_any_explicit = true\n")
    path = tmp_path / "example.py"
    path.write_text(
        "import json\n\ndef first(text: str):\n"
        "    parsed = json.loads(text)\n    data = parsed['one']\n    print(data)\n    return data\n"
        "\ndef second(text: str):\n"
        "    parsed = json.loads(text)\n    data = parsed['two']\n    print(data)\n    return data\n"
    )
    checker = MypyInferrer()
    try:
        assert checker.check(str(path), path.read_text()) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        proposal = engine.analyze_file(str(path))[0]
        result = engine.apply_refactoring(str(path), proposal)
        helper = next(
            n
            for n in ast.walk(ast.parse(result))
            if isinstance(n, ast.FunctionDef) and "extracted_func" in n.name
        )
        assert helper.returns is None and all(arg.annotation is None for arg in helper.args.args)
        assert checker.check(str(path), result) == CheckSuccess()
        path.write_text(result)
        independent = subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "mypy",
                "--no-incremental",
                "--cache-dir",
                str(tmp_path / "cache"),
                str(path),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert independent.returncode == 0, independent.stdout + independent.stderr
    finally:
        checker.close()


def test_missing_optional_mypy_has_no_unraisable_destructor(tmp_path: Path) -> None:
    source_root = Path(type_inference.__file__).resolve().parents[1]
    code = (
        f"import sys; sys.path.insert(0, {str(source_root)!r})\n"
        "from towel.type_inference import MypyInferrer\n"
        "try:\n    MypyInferrer()\nexcept ImportError:\n    print('absent')\n"
    )
    result = subprocess.run(
        [sys.executable, "-P", "-S", "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0 and result.stdout.strip() == "absent"
    assert result.stderr == ""


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_mypy_workers_leave_caller_gc_state_alone(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("x: int = 1\n")
    code = f"""import gc
from towel.type_inference import MypyInferrer
path = {str(path)!r}
first, second = MypyInferrer(), MypyInferrer()
gc.disable()
gc.freeze()
assert gc.get_freeze_count() > 0
try:
    first.check(path, "x: int = 1\\n")
    second.check(path, "x: int = 1\\n")
    first.close()
    assert not gc.isenabled()
    assert gc.get_freeze_count() > 0
    second.close()
    assert not gc.isenabled()
    assert gc.get_freeze_count() > 0
finally:
    first.close()
    second.close()
    gc.unfreeze()
    gc.enable()
"""
    result = subprocess.run(
        [sys.executable, "-P", "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_cold_concurrent_calls_and_close_reap_workers(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    source = "def f(x: int) -> str:\n    return x\n"
    path.write_text(source)
    first, second = MypyInferrer(), MypyInferrer()
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(
                pool.map(lambda checker: checker.check(str(path), source), [first, first, second])
            )
        assert all(isinstance(result, CheckSuccess) and result.errors for result in results)
        processes = [first._process, second._process]
        CombinedOracle(first, [second]).close()
        assert all(process is not None and process.poll() is not None for process in processes)
        assert isinstance(first.check(str(path), source), CheckFailure)
    finally:
        first.close()
        second.close()


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
def test_timeout_is_unknown_and_never_certifies_generated_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "m.py"
    path.write_text(
        "def first(x: int) -> str:\n    out = str(x)\n    print(out)\n    return out\n\n"
        "def second(x: str) -> str:\n    out = str(x)\n    print(out)\n    return out\n"
    )
    checker = PyrightOracle(language_server=False)
    assert checker.is_subtype(str(path), path.read_text(), [("str", "int")]) == [Subtyping.NO]
    monkeypatch.setattr(type_inference, "PYRIGHT_TIMEOUT_SECONDS", 0.000001)
    assert checker.is_subtype(str(path), path.read_text(), [("str", "int")]) == [Subtyping.UNKNOWN]
    assert isinstance(checker.check(str(path), path.read_text()), CheckFailure)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=checker
    )
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(RefactoringError, match="type check failed"):
        engine.apply_refactoring(str(path), proposal)


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
def test_parent_pyright_config_selects_pyright_without_packaging_metadata(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"typeCheckingMode": "strict"}')
    package = tmp_path / "package"
    package.mkdir()
    path = package / "m.py"
    path.write_text("x = 1\n")
    choice = type_oracle_for_project(path)
    assert isinstance(choice.tool, PyrightOracle)
    choice.tool.close()


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
def test_hidden_project_stubs_are_part_of_the_prospective_graph(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"stubPath":".stubs","reportMissingImports":false,"reportMissingModuleSource":false}'
    )
    stubs = tmp_path / ".stubs"
    stubs.mkdir()
    (stubs / "service.pyi").write_text("def value() -> str: ...\n")
    path = tmp_path / "example.py"
    source = "import service\nvalue: int = service.value()\n"
    path.write_text(source)
    result = PyrightOracle().check(str(path), source)
    assert isinstance(result, CheckSuccess)
    assert any("reportAssignmentType" in error.message for error in result.errors)


def test_checker_children_ignore_source_pythonpath_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    oracle: TypeOracle,
) -> None:
    marker = tmp_path / "executed"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    source = tmp_path / "m.py"
    source.write_text("x: int = 1\n")
    monkeypatch.setenv("PYTHONPATH", str(hooks))
    result = oracle.check(str(source), source.read_text())
    assert isinstance(result, CheckSuccess), result
    assert not marker.exists(), "checker startup executed the analyzed project's Python hook"


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_parent_mypy_config_applies_beyond_an_unrelated_pyproject(tmp_path: Path) -> None:
    (tmp_path / "mypy.ini").write_text("[mypy]\ndisallow_untyped_defs = True\n")
    child = tmp_path / "package"
    child.mkdir()
    (child / "pyproject.toml").write_text('[project]\nname="example"\nversion="1"\n')
    path = child / "m.py"
    path.write_text("def value(x: int) -> int:\n    return x\n")
    checker = MypyInferrer()
    try:
        assert checker.check(str(path), path.read_text()) == CheckSuccess()
        result = checker.check(str(path), "def value(x):\n    return x\n")
        assert isinstance(result, CheckSuccess)
        assert any("missing a type annotation" in error.message for error in result.errors)
    finally:
        checker.close()


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
def test_shared_pyright_extends_config_is_preserved(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "strict.json").write_text(
        '{// shared rules, including a trailing comma\n"typeCheckingMode":"strict",\n}'
    )
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyrightconfig.json").write_text('{"extends":"../config/strict.json"}')
    path = root / "m.py"
    path.write_text("def value(x: int) -> int:\n    return x\n")
    checker = PyrightOracle()
    assert checker.check(str(path), path.read_text()) == CheckSuccess()
    result = checker.check(str(path), "def value(x):\n    return x\n")
    assert isinstance(result, CheckSuccess)
    assert any("reportUnknownParameterType" in error.message for error in result.errors)


@pytest.mark.parametrize(
    "diagnostic, status",
    [
        ({"file": "m.py", "severity": "error", "message": "no position"}, 1),
        ({"file": "m.py", "severity": "mystery", "message": "unrecognized"}, 0),
        (None, 1),
        (
            {
                "file": "m.py",
                "severity": "error",
                "message": "bad line",
                "range": {"start": {"line": -1}},
            },
            1,
        ),
        (
            {
                "file": "m.py",
                "severity": "error",
                "message": "wrong status",
                "range": {"start": {"line": 1}},
            },
            0,
        ),
    ],
)
def test_invalid_pyright_results_do_not_certify_subtypes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic: object,
    status: int,
) -> None:
    import json

    checker = PyrightOracle.__new__(PyrightOracle)
    checker._command = ["unused"]
    checker._server = None
    checker._warmed = {}
    checker._probe_copies = {}
    path = tmp_path / "m.py"
    path.write_text("x = 1\n")
    payload = json.dumps({"generalDiagnostics": [] if diagnostic is None else [diagnostic]})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=status, stdout=payload, stderr=""
        ),
    )
    try:
        assert checker.is_subtype(str(path), path.read_text(), [("str", "int")]) == [
            Subtyping.UNKNOWN
        ]
        assert isinstance(checker.check(str(path), path.read_text()), CheckFailure)
    finally:
        checker.close()


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_worker_timeout_covers_a_blocked_request_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import signal
    import time

    path = tmp_path / "m.py"
    path.write_text("x: int = 1\n")
    checker = MypyInferrer()
    try:
        assert checker.check(str(path), path.read_text()) == CheckSuccess()
        process = checker._process
        assert process is not None
        os.kill(process.pid, signal.SIGSTOP)
        monkeypatch.setattr(type_inference, "MYPY_TIMEOUT_SECONDS", 0.1)
        start = time.monotonic()
        result = checker.check(str(path), "#" + "x" * (2 * 1024 * 1024) + "\nx: int = 1\n")
        assert isinstance(result, CheckFailure) and "timed out" in result.reason
        assert time.monotonic() - start < 8
        assert process.poll() is not None
    finally:
        checker.close()


@pytest.mark.parametrize("inside_project", [False, True])
def test_relocated_checks_keep_input_rules_output_changes_and_consumers(
    tmp_path: Path,
    oracle: TypeOracle,
    inside_project: bool,
) -> None:
    import shutil
    from towel.type_inference import relocate_oracle

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.mypy]\nfiles = ["src", "consumer.py"]\nstrict = true\n'
        '[tool.pyright]\ntypeCheckingMode = "strict"\n'
    )
    source = project / "src"
    source.mkdir()
    (source / "__init__.py").write_text("")
    host, peer = source / "host.py", source / "peer.py"
    host.write_text("def value() -> str:\n    return 'one'\n")
    peer.write_text("from .host import value\ntext: str = value()\n")
    consumer = project / "consumer.py"
    consumer.write_text("from src.host import value\ntext: str = value()\n")
    destination = (project if inside_project else tmp_path) / "cleaned"
    shutil.copytree(source, destination)
    checker = relocate_oracle(oracle, source, destination)
    output_host, output_peer = destination / "host.py", destination / "peer.py"
    assert checker.check(str(output_host), output_host.read_text()) == CheckSuccess()
    # A previous change in another output file must remain visible to this check.
    output_host.write_text(host.read_text() + "\ndef helper() -> str:\n    return 'two'\n")
    result = checker.check(str(output_peer), "from .host import helper\ntext: str = helper()\n")
    assert result == CheckSuccess(), result
    changed = checker.check(str(output_host), "def value() -> int:\n    return 1\n")
    assert isinstance(changed, CheckSuccess)
    assert any(error.path == str(consumer) for error in changed.errors)
    assert any(error.path == str(output_peer) for error in changed.errors)
    # Input sources and imports were never physically replaced during checking.
    assert "helper" not in host.read_text()
