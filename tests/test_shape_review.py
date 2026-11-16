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

"""Executable regressions from the independent shape round-two review."""

from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_cli_integration import CliResult, invoke


def dry(source: Path, output: Path, strict: bool, *options: str) -> CliResult:
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--strict" if strict else "--no-strict",
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
            *options,
        ]
    )
    assert result.status == 0, result
    return result


def observe(root: Path, driver: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("strict", [False, True])
def test_return_permutation_preserves_strict_finalizer_order(tmp_path: Path, strict: bool) -> None:
    """Review 3: redirect return permutations must preserve strict frame-slot order."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "mod.py").write_text("""events = []
class Noisy:
    def __init__(self, label): self.label = label
    def __del__(self): events.append(f"del {self.label}")

def total():
    first = Noisy("1")
    second = Noisy("2")
    events.append("built")
    return second, first

def report():
    first = Noisy("1")
    second = Noisy("2")
    events.append("built")
    events.append("after block")
    return 0
""")
    driver = "import mod; mod.report(); print(mod.events)"
    before = observe(source, driver)
    assert before == "['built', 'after block', 'del 1', 'del 2']\n"
    output = tmp_path / "out"
    dry(source, output, strict, "--complete-scope")
    after = observe(output, driver)
    if strict:
        assert after == before
    else:
        assert "built" in after and "after block" in after


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("indirect", [False, True])
def test_forward_redirect_is_bound_before_import_time_calls(
    tmp_path: Path, strict: bool, indirect: bool
) -> None:
    """Review 1: a caller may run before a later equivalent function is defined."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    boot = "BOOT = report([1, 2], [])"
    if indirect:
        boot = "def boot():\n    return report([1, 2], [])\nBOOT = boot()"
    (source / "mod.py").write_text(
        """def report(order, log):
    log.append("report")
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)
    return tuple(log)

"""
        + boot
        + """

def total(order, log):
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)
"""
    )
    before = observe(source, "import mod; print(mod.BOOT)")
    assert before == "('report', 2, 6)\n"
    output = tmp_path / "out"
    dry(source, output, strict)
    assert observe(output, "import mod; print(mod.BOOT)") == before


REDIRECT_SOURCE = """def total(order, log):
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)

def report(order):
    log = []
    if not order:
        raise ValueError("empty")
    log.append(len(order))
    log.append(sum(order) * 2)
    return f"amount={log[-1]}"
"""


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "mutation",
    [
        "with mock.patch(target='original.total', new=replacement):\n    print(original.report([1, 2]))",
        "with mock.patch.object(target=original, attribute='total', new=replacement):\n    print(original.report([1, 2]))",
        "with mock.patch(f'{original.__name__}.total', replacement):\n    print(original.report([1, 2]))",
        "target = 'original.total'\nwith mock.patch(target, replacement):\n    print(original.report([1, 2]))",
        "attr = 'total'\nwith mock.patch.object(original, attr, replacement):\n    print(original.report([1, 2]))",
        "with mock.patch.dict(original.__dict__, {'total': replacement}):\n    print(original.report([1, 2]))",
        "with mock.patch.multiple(original, **{'total': replacement}):\n    print(original.report([1, 2]))",
        "ns = original.__dict__\nns['total'] = replacement\nprint(original.report([1, 2]))",
        "args = (original, 'total', replacement)\nsetattr(*args)\nprint(original.report([1, 2]))",
        "original.__setattr__('total', replacement)\nprint(original.report([1, 2]))",
        "p = mock.patch\nwith p('original.total', replacement):\n    print(original.report([1, 2]))",
        "p = setattr\np(original, 'total', replacement)\nprint(original.report([1, 2]))",
        "mp = pytest.MonkeyPatch()\nmp.setattr(target=original, name='total', value=replacement)\nprint(original.report([1, 2]))\nmp.undo()",
        "mp = pytest.MonkeyPatch()\nmp.delattr(target='original.total')\nprint(original.report([1, 2]))\nmp.undo()",
    ],
)
def test_patch_forms_preserve_independent_callers(
    tmp_path: Path, strict: bool, mutation: str
) -> None:
    """Review 2: patching an original must not change its former duplicate's behavior."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "original.py").write_text(REDIRECT_SOURCE)
    (source / "client.py").write_text(
        "import unittest.mock as mock, original, pytest\n"
        "replacement = lambda order, log: log.append(0)\n" + mutation + "\n"
    )
    before = observe(source, "import client")
    assert before == "amount=6\n"
    output = tmp_path / "out"
    dry(source, output, strict)
    assert observe(output, "import client") == before


@pytest.mark.parametrize("strict", [False, True])
def test_helper_merge_reads_sibling_keyword_callers(tmp_path: Path, strict: bool) -> None:
    """Review 4a: merging a selected file must not invalidate a sibling's keywords."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "sample.py").write_text(
        "def __extracted_func_0(add_attribute, function):\n"
        "    if callable(function):\n        return add_attribute(function)\n"
        "    return add_attribute\n\n"
        "def __extracted_func_1(actual_decorator, message):\n"
        "    if callable(message):\n        return actual_decorator(message)\n"
        "    return actual_decorator\n"
    )
    client = "import sample; print(sample.__extracted_func_1(actual_decorator=str, message=42))"
    (source / "caller.py").write_text(client)
    before = observe(source, client)
    output = tmp_path / "out"
    output.mkdir()
    dry(source / "sample.py", output / "sample.py", strict)
    assert observe(output, client) == before


@pytest.mark.parametrize("strict", [False, True])
def test_src_package_scans_consumers_outside_import_root(tmp_path: Path, strict: bool) -> None:
    """Review 4b: src is an import root, not evidence of complete consumer scope."""
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "mod.py").write_text(REDIRECT_SOURCE)
    tests = tmp_path / "tests"
    tests.mkdir()
    client = (
        "import pkg.mod; pkg.mod.total = lambda o, l: l.append(0); print(pkg.mod.report([1, 2]))"
    )
    (tests / "test_mod.py").write_text(client)
    before = observe(package.parent, client)
    output = tmp_path / "out" / "pkg"
    output.mkdir(parents=True)
    (output / "__init__.py").write_text("")
    dry(package / "mod.py", output / "mod.py", strict)
    assert observe(output.parent, client) == before == "amount=6\n"


def test_complete_scope_includes_siblings_of_every_selected_file(tmp_path: Path) -> None:
    """Review 4c: selecting a second file must not hide sibling consumers."""
    from towel.unification.rebinding import rebinding_scope

    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    for path in paths:
        path.write_text(REDIRECT_SOURCE)
    client = tmp_path / "client.py"
    client.write_text("import a; a.total = lambda o, l: l.append(0)\n")
    complete, sources = rebinding_scope([str(path) for path in paths], declared_complete=True)
    assert complete and str(client) in sources


@pytest.mark.parametrize("strict", [False, True])
def test_quick_preview_reads_sibling_rebindings(tmp_path: Path, strict: bool) -> None:
    """Review 4d: quick preview must use dry's consumer scope for redirects."""
    path = tmp_path / "original.py"
    path.write_text(REDIRECT_SOURCE)
    (tmp_path / "client.py").write_text(
        "import original; original.total = lambda o, l: l.append(0)\n"
    )
    result = invoke(
        [
            "preview",
            "--quick",
            str(path),
            "--strict" if strict else "--no-strict",
            "--complete-scope" if strict else "--no-types",
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    assert "Reuse total" not in result.stdout
    assert "Found 0" not in result.stdout


@pytest.mark.parametrize("strict", [False, True])
def test_thunk_parameter_order_matches_first_textual_use(tmp_path: Path, strict: bool) -> None:
    """Review 5: a thunk used before a literal must precede that literal in the helper."""
    source = tmp_path / "mod.py"
    source.write_text("""def first(flag):
    local_scale = 3
    head = compute_a() if flag else "fallback"
    mid = head.upper()
    tail = len(mid) * local_scale + 10
    return f"{mid}:{tail}"

def second(flag):
    local_scale = 3
    head = compute_b() if flag else "fallback"
    mid = head.upper()
    tail = len(mid) * local_scale + 20
    return f"{mid}:{tail}"

def compute_a(): return "one"
def compute_b(): return "two"
""")
    before = observe(tmp_path, "import mod; print(mod.first(True), mod.second(False))")
    output = tmp_path / "out" / "mod.py"
    dry(source, output, strict)
    text = output.read_text()
    import ast

    helpers = [
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
    ]
    assert helpers or strict  # strict can decline the ownership transfer
    for helper in helpers:
        names = [arg.arg for arg in (*helper.args.posonlyargs, *helper.args.args)]
        assert names.index("_param_0") < names.index("_param_1")
    assert observe(output.parent, "import mod; print(mod.first(True), mod.second(False))") == before


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("indent", ["  ", "\t"])
def test_ruff_preserves_multiline_arguments_at_project_indentation(
    tmp_path: Path, strict: bool, indent: str
) -> None:
    """Review 6a: synthetic formatter suites must not indent string contents."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="0"\nrequires-python=">=3.11"\n'
        "[tool.ruff]\nindent-width = 2\n"
        + ('[tool.ruff.format]\nindent-style = "tab"\n' if indent == "\t" else "")
    )
    (source / "mod.py").write_text(
        'def first():\n  text = """first\n  second\nlast"""\n'
        '  text = text.strip()\n  text = text.upper()\n  return text + "!"\n\n'
        'def second():\n  text = """other\n  sample\nend"""\n'
        '  text = text.strip()\n  text = text.upper()\n  return text + "?"\n'.replace(
            "\n  ", "\n" + indent
        )
    )
    driver = "import mod; print(repr((mod.first(), mod.second())))"
    before = observe(source, driver)
    output = tmp_path / "out"
    result = dry(source, output, strict)
    assert "formatter changed its code" not in result.stdout
    assert "__extracted_func_0" in (output / "mod.py").read_text()
    assert observe(output, driver) == before


@pytest.mark.parametrize("strict", [False, True])
def test_unconfigured_formatter_note_offers_configuration(tmp_path: Path, strict: bool) -> None:
    """Review 6b: installing an optional formatter does not configure the project."""
    source = tmp_path / "mod.py"
    source.write_text(REDIRECT_SOURCE)
    result = dry(source, tmp_path / "out.py", strict)
    assert "no formatter configured" in result.stderr
    assert "Configure Ruff or Black" in result.stderr
    assert "Install the format extra" not in result.stderr


@pytest.mark.parametrize("strict", [False, True])
def test_excluded_sources_do_not_block_redirects(tmp_path: Path, strict: bool) -> None:
    """Review 6c: the rebinding scan must honor the command's source exclusions."""
    source = tmp_path / "src"
    broken = source / "broken_dir"
    broken.mkdir(parents=True)
    (broken / "py2.py").write_text('print "python two"\n')
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "mod.py").write_text(REDIRECT_SOURCE)
    before = observe(source, "import mod; print(mod.report([1, 2]))")
    output = tmp_path / "out"
    result = dry(source, output, strict, "--exclude", "broken_dir")
    assert "cannot be analyzed" not in result.stderr
    assert "    total(order, log)" in (output / "mod.py").read_text()
    assert observe(output, "import mod; print(mod.report([1, 2]))") == before


def test_unanchored_library_selection_has_no_implicit_complete_scope(tmp_path: Path) -> None:
    """A library file selection is explicit; CLI entry points supply their consumer root."""
    from towel.unification.rebinding import rebinding_scope

    selected = tmp_path / "mod.py"
    selected.write_text(REDIRECT_SOURCE)
    unrelated = tmp_path / "other.py"
    unrelated.write_text('print "old syntax"\n')
    complete, sources = rebinding_scope([str(selected)], declared_complete=False)
    assert not complete and set(sources) == {str(selected)}
    _, program = rebinding_scope([str(selected)], declared_complete=False, program_root=tmp_path)
    assert str(unrelated) in program


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("kind", ["script", "symlink_file", "symlink_directory"])
def test_nonstandard_consumers_cannot_hide_rebindings(
    tmp_path: Path, strict: bool, kind: str
) -> None:
    """Shape review: read-only consumers include scripts and cannot silently omit links."""
    source = tmp_path / "project"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "mod.py").write_text(REDIRECT_SOURCE)
    consumer = "#!/usr/bin/env python3\nimport mod\nmod.total = lambda o, l: l.append(0)\nprint(mod.report([1, 2]))\n"
    if kind == "script":
        script = source / "bin" / "cli"
        script.parent.mkdir()
        script.write_text(consumer)
    else:
        external = tmp_path / "external"
        external.mkdir()
        (external / "client.py").write_text(consumer)
        if kind == "symlink_file":
            script = source / "client.py"
            script.symlink_to(external / "client.py")
        else:
            (source / "scripts").symlink_to(external, target_is_directory=True)
            script = source / "scripts" / "client.py"
    driver = f"import runpy; runpy.run_path({str(script)!r})"
    before = observe(source, driver)
    output = tmp_path / "out" / "mod.py"
    dry(source / "mod.py", output, strict)
    assert observe(output.parent, driver) == before == "amount=6\n"
