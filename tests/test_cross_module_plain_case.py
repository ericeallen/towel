# Copyright 2026 Eric Allen
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

"""``--cross-module`` shares the plain case, and says why when it shares nothing.

Audit finding 2 (engine map F; scenario 37, probes p6 and p7): with
``--cross-module`` Towel shared nothing on any designed case or on three real
projects, and said so one reason code per attempt. Since 2.0:

- when it shares nothing across modules, one message names, for each pair of
  modules, the condition that failed, and the one remedy that exists;
- a module name that every participating module binds by the same absolute
  import (``import re`` in both) is read bare in the helper, in relaxed mode;
- in relaxed mode a builtin is read bare in the helper unless a participating
  module binds that name (docs/design/DECISIONS.md, "Relaxed mode reads a
  builtin bare across modules");
- in relaxed mode an immutable literal moves into the shared helper with the
  code around it, as it does within one module.

Strict mode keeps 1.949's refusals and ``--parameterize-builtins``.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

import towel.cli

from tests.test_cli_integration import invoke
from tests.test_callback_parameters import FIXTURES, helper_calls, run_script

pytestmark = pytest.mark.relaxed_mode


def _scenario(tmp_path: Path, fixture: str) -> Tuple[Path, Path]:
    case = tmp_path / Path(fixture).name
    for path in sorted((FIXTURES / fixture).rglob("*.py.txt")):
        target = case / path.relative_to(FIXTURES / fixture).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return case / "src", case / "out"


def _dry(source: Path, output: Path, *options: str) -> Tuple[str, str]:
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--cross-module",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            *options,
        ]
    )
    assert result.status == 0, result.stderr
    generated = "\n".join(
        f"# {path.relative_to(output)}\n{path.read_text()}" for path in sorted(output.rglob("*.py"))
    )
    return result.stdout, generated


def _write(root: Path, files: Dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _helpers(generated: str) -> List[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(generated))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]


# -- One message ---------------------------------------------------------------


def test_sharing_nothing_names_each_failed_condition_and_the_remedy(tmp_path: Path) -> None:
    """Probe p6: two sibling modules of a package, neither importing the other."""
    source, output = _scenario(tmp_path, "reach/p6_xmod")
    printed, _ = _dry(source, output)
    assert printed.count("--cross-module shared nothing between modules") == 1
    message = printed[printed.index("--cross-module shared nothing between modules") :]
    assert "pkg/contacts.py and pkg/labels.py" in message
    assert "would replace the attribute contacts of package pkg" in message or (
        "would replace the attribute labels of package pkg" in message
    )
    assert "already imports" in message and "at its top" in message
    # One line per pair of modules and condition, not one per pair of blocks.
    assert message.count("pkg/contacts.py and pkg/labels.py") == 1


def test_the_remedy_works(tmp_path: Path) -> None:
    """With ``import pkg.contacts`` at the top of labels.py, p6 shares its loop."""
    source, output = _scenario(tmp_path, "reach/p6_xmod")
    labels = source / "pkg" / "labels.py"
    labels.write_text(
        labels.read_text().replace('"""Labels."""\n', '"""Labels."""\n\nimport pkg.contacts\n')
    )
    printed, generated = _dry(source, output)
    assert "shared nothing" not in printed
    if towel.cli.DEFAULT_STRICT:
        assert len(_helpers(generated)) == 1
    else:
        # unique_tags is the whole body of unique_emails, so relaxed mode calls
        # it (shape's redirect) instead of a new helper.
        assert _helpers(generated) == []
        assert "return unique_emails(emails)" in generated
    driver = source.parent / "driver.py"
    assert run_script(driver, str(output)) == run_script(driver, str(source))


# -- A module both modules import alike ------------------------------------------

WORDS = """

def {name}(rows, text):
    words = re.split(r"\\s+", text.strip())
    joined = "-".join(words)
    found = re.findall(r"[a-z]+", joined)
    return found + rows
"""

FAKE_RE = """
def split(pattern, text):
    return ["fake"]


def findall(pattern, text):
    return ["fake", text]
"""


def _two_modules(
    tmp_path: Path, first_prelude: str, second_prelude: str, body: str = WORDS, **extra: str
) -> Path:
    root = tmp_path / "src"
    _write(
        root,
        {
            "pkg/__init__.py": "",
            "pkg/fake_re.py": FAKE_RE,
            "pkg/first.py": first_prelude + body.format(name="first"),
            "pkg/second.py": "import pkg.first\n"
            + second_prelude
            + body.format(name="second")
            + extra.get("second_extra", ""),
        },
    )
    _write(
        tmp_path,
        {
            "driver.py": "import sys\nsys.path.insert(0, sys.argv[1])\n"
            "from pkg import first, second\n"
            "print(first.first(['x'], ' a b  c '), second.second(['y'], 'one two'))\n"
        },
    )
    return root


def _parameters(helper: ast.FunctionDef) -> List[str]:
    return [argument.arg for argument in helper.args.posonlyargs + helper.args.args]


def _same(tmp_path: Path, source: Path) -> bool:
    driver = tmp_path / "driver.py"
    return run_script(driver, str(tmp_path / "out")) == run_script(driver, str(source))


def test_a_module_both_import_alike_is_read_bare(tmp_path: Path) -> None:
    source = _two_modules(tmp_path, "import re\n", "import re\n")
    _, generated = _dry(source, tmp_path / "out")
    (helper,) = _helpers(generated)
    assert "re" not in _parameters(helper)
    assert "re.split(" in ast.unparse(helper)
    assert _same(tmp_path, source)


def test_strict_mode_still_passes_a_module_it_reads(tmp_path: Path) -> None:
    source = _two_modules(tmp_path, "import re\n", "import re\n")
    _, generated = _dry(source, tmp_path / "out", "--strict")
    for helper in _helpers(generated):
        assert "re.split(" not in ast.unparse(helper)
    assert _same(tmp_path, source)


@pytest.mark.parametrize(
    "second_prelude",
    [
        "import pkg.fake_re as re\n",  # another module under the same name
        "re = __import__('re')\n",  # module data, not an import
        "from pkg import fake_re as re\n",  # an attribute read once, not an import
    ],
    ids=["other_module", "assigned", "from_import"],
)
def test_a_module_name_bound_otherwise_in_one_module_is_still_passed(
    tmp_path: Path, second_prelude: str
) -> None:
    source = _two_modules(tmp_path, "import re\n", second_prelude)
    _, generated = _dry(source, tmp_path / "out")
    for helper in _helpers(generated):
        assert "re.split(" not in ast.unparse(helper) or "re" in _parameters(helper)
    assert _same(tmp_path, source)


LIBRARY_CALLS = """

def {name}(value):
    first = library.transform(value)
    second = library.transform(first)
    third = library.transform(second)
    fourth = library.transform(third)
    return fourth
"""

LIBRARY = {
    "library/__init__.py": "def transform(value):\n    return value + 10\n",
    "library/sub.py": "def transform(value):\n    return value + 100\n",
}


def _library_project(tmp_path: Path, first_prelude: str, second_prelude: str, tail: str) -> Path:
    """``pkg.first`` and ``pkg.second`` each call ``library.transform`` four times."""
    root = _write(
        tmp_path / "src",
        {
            **LIBRARY,
            "pkg/__init__.py": "",
            "pkg/first.py": first_prelude + LIBRARY_CALLS.format(name="first"),
            "pkg/second.py": "import pkg.first\n"
            + second_prelude
            + LIBRARY_CALLS.format(name="second")
            + tail,
        },
    )
    _write(
        tmp_path,
        {
            "driver.py": "import sys\nsys.path.insert(0, sys.argv[1])\n"
            "from pkg import first, second\n"
            "print(first.first(1), second.second(1))\n"
        },
    )
    return root


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_a_dotted_import_binds_its_package_not_the_submodule_an_alias_names(
    tmp_path: Path, strict: bool
) -> None:
    """Review of lane reach, finding 2: ``import library.sub`` binds the package ``library``.

    ``import library.sub as library`` binds the submodule. The two statements
    name the same module but bind different objects to ``library``, so the
    borrower's ``library.transform`` (adds 100) must not become the host's
    (adds 10): before the fix relaxed mode printed ``41 41``.
    """
    source = _library_project(
        tmp_path, "import library.sub\n", "import library.sub as library\n", ""
    )
    _dry(source, tmp_path / "out", *(["--strict"] if strict else []))
    driver = tmp_path / "driver.py"
    assert run_script(driver, str(source)) == (0, "41 401\n", "")
    assert run_script(driver, str(tmp_path / "out")) == (0, "41 401\n", "")


def test_dotted_imports_that_bind_the_same_package_are_alike(tmp_path: Path) -> None:
    """``import library`` and ``import library.sub`` both bind the package ``library``."""
    source = _library_project(tmp_path, "import library\n", "import library.sub\n", "")
    _, generated = _dry(source, tmp_path / "out")
    (helper,) = _helpers(generated)
    assert "library" not in _parameters(helper)
    driver = tmp_path / "driver.py"
    assert run_script(driver, str(tmp_path / "out")) == run_script(driver, str(source))


LATE_IMPORT_TAIL = """

try:
    print(second(1))
except NameError:
    print("not ready")

import library
"""


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_an_import_that_has_not_run_yet_is_not_read_from_the_host(
    tmp_path: Path, strict: bool
) -> None:
    """Review of lane reach, finding 3: the borrower calls its function before ``import library``.

    The original call raises ``NameError``; a helper reading the host's
    ``library`` would succeed (relaxed mode printed ``41`` before the fix).
    The borrower's import binds the name only after the call can run, so the
    lookup stays with the caller.
    """
    source = _library_project(tmp_path, "import library\n", "", LATE_IMPORT_TAIL)
    _dry(source, tmp_path / "out", *(["--strict"] if strict else []))
    driver = tmp_path / "driver.py"
    expected = (0, "not ready\n41 41\n", "")
    assert run_script(driver, str(source)) == expected
    assert run_script(driver, str(tmp_path / "out")) == expected


def test_an_import_above_the_function_is_still_read_bare(tmp_path: Path) -> None:
    """The same borrower with its import first: the helper reads the host's ``library``."""
    tail = LATE_IMPORT_TAIL.replace("\nimport library\n", "\n")
    source = _library_project(tmp_path, "import library\n", "import library\n", tail)
    _, generated = _dry(source, tmp_path / "out")
    (helper,) = _helpers(generated)
    assert "library" not in _parameters(helper)
    driver = tmp_path / "driver.py"
    assert run_script(driver, str(tmp_path / "out")) == run_script(driver, str(source))


# -- Builtins across modules -----------------------------------------------------

COUNTS = """

def {name}(rows, text):
    words = re.split(r"\\s+", text.strip())
    count = len(words)
    total = sum(len(row) for row in rows)
    return [count, total, sorted(words)]
"""


def test_relaxed_mode_reads_a_builtin_bare_across_modules(tmp_path: Path) -> None:
    source = _two_modules(tmp_path, "import re\n", "import re\n", COUNTS)
    printed, generated = _dry(source, tmp_path / "out")
    (helper,) = _helpers(generated)
    text = ast.unparse(helper)
    assert "len(words)" in text and "sum(" in text and "sorted(words)" in text
    assert not {"len", "sum", "sorted"} & set(_parameters(helper))
    assert "builtin_may_differ_by_module" not in printed
    assert _same(tmp_path, source)


@pytest.mark.parametrize(
    "binding",
    [
        "\n\ndef len(value):\n    return 99\n",
        "\n\nlen = lambda value: 99\n",
        "\n\ndef rebind():\n    global len\n    len = lambda value: 99\n",
        "\n\nfrom os.path import *\n",
    ],
    ids=["def", "assignment", "global", "star_import"],
)
def test_a_builtin_a_participating_module_binds_is_not_read_bare(
    tmp_path: Path, binding: str
) -> None:
    """``second`` binds its own ``len`` (or may): the helper may not read the host's."""
    source = _two_modules(tmp_path, "import re\n", "import re\n", COUNTS, second_extra=binding)
    printed, generated = _dry(source, tmp_path / "out")
    for helper in _helpers(generated):
        assert "len(" not in ast.unparse(helper)
    assert _same(tmp_path, source)


def test_strict_mode_still_refuses_a_builtin_across_modules(tmp_path: Path) -> None:
    source = _two_modules(tmp_path, "import re\n", "import re\n", COUNTS)
    printed, generated = _dry(source, tmp_path / "out", "--strict")
    assert "builtin_may_differ_by_module" in printed
    for helper in _helpers(generated):
        assert "len(" not in ast.unparse(helper)


def test_relaxed_mode_gives_up_a_builtin_rebound_by_code_outside_the_project(
    tmp_path: Path,
) -> None:
    """The counterexample of the builtin rule: a dependency rebinds ``b.len`` mid-block.

    The original reads the new ``len`` after the callback; the helper, in
    ``a``, reads ``a``'s. Strict mode keeps the lookup at the caller and the
    output; relaxed mode changes exactly this line of it.
    """
    body = (
        "def {name}(values, callback):\n"
        "    assert values is not None\n"
        "    assert callback is not None\n"
        "    return (len(values) * 1000 + (callback(), len(values))[1]) * {factor}\n"
    )
    files = {
        "pyproject.toml": '[project]\nname="probe"\nversion="0"\n',
        "pkg/__init__.py": "",
        "pkg/a.py": body.format(name="first", factor=5),
        "pkg/b.py": "import pkg.a\n" + body.format(name="second", factor=7),
        "check.py": "from pkg import a, b\nfrom outside import install\n"
        "print(a.first([1, 3], lambda: None), b.second([2, 4], install))\n",
    }
    vendor = _write(
        tmp_path / "vendor",
        {"outside.py": ("def install():\n    from pkg import b\n    b.len = lambda values: 101\n")},
    )
    outputs = {}
    for mode in ("before", "relaxed", "strict"):
        root = _write(tmp_path / mode, files)
        if mode != "before":
            _dry(root / "pkg", root / "pkg", *(["--strict"] if mode == "strict" else []))
        completed = subprocess.run(
            [sys.executable, "check.py"],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(vendor)},
            capture_output=True,
            text=True,
            check=True,
        )
        outputs[mode] = completed.stdout
    assert outputs["before"] == "10010 14707\n"
    assert outputs["strict"] == outputs["before"]
    assert outputs["relaxed"] == "10010 14014\n"


# -- Scenario 37 and literals ------------------------------------------------------


def test_a_function_copied_into_two_modules_becomes_one_function_and_plain_calls(
    tmp_path: Path,
) -> None:
    """Audit scenario 37: ``_reference`` copied character for character into orders and invoices.

    invoices imports orders at its top, so orders holds the helper; ``re`` is
    imported alike by both, ``len`` is bound by neither, and the regular
    expression moves with the code.
    """
    source, output = _scenario(tmp_path, "reach/37_cross_identical")
    _, generated = _dry(source, output)
    (helper,) = _helpers(generated)
    assert sorted(_parameters(helper)) == ["customer", "number", "prefix"]
    text = ast.unparse(helper)
    assert "re.sub('[^a-z0-9]+'" in text and "len(slug)" in text
    calls = helper_calls(generated)
    assert len(calls) == 2
    assert all(
        all(isinstance(argument, ast.Name) for argument in ast.parse(call).body[0].value.args)  # type: ignore[attr-defined]
        for call in calls
    )
    driver = source.parent / "driver.py"
    assert run_script(driver, str(output)) == run_script(driver, str(source))


def test_strict_mode_keeps_its_cross_module_refusals_on_scenario_37(tmp_path: Path) -> None:
    source, output = _scenario(tmp_path, "reach/37_cross_identical")
    printed, generated = _dry(source, output, "--strict")
    # Strict mode declines it before the builtin is asked about: the order
    # the function releases its objects in.
    assert _helpers(generated) == []
    assert "owned_binding_frame_boundary" in printed
    driver = source.parent / "driver.py"
    assert run_script(driver, str(output)) == run_script(driver, str(source))


LITERALS = """

def {name}(rows, text):
    label = "total-" + text
    words = text.split("-")
    joined = ", ".join(words)
    return [label, joined, rows]
"""


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_equal_literals_move_into_a_helper_in_another_module_only_in_relaxed_mode(
    tmp_path: Path, strict: bool
) -> None:
    source = _two_modules(tmp_path, "", "", LITERALS)
    _, generated = _dry(source, tmp_path / "out", *(["--strict"] if strict else []))
    calls = helper_calls(generated)
    if strict:
        # Each common literal stays in its own module's pool, in a tuple.
        assert all("('total-'" in call or "lambda" in call for call in calls) or not calls
    else:
        # The two bodies are equal, so relaxed mode calls first from the other
        # module (shape's redirect): first's literals serve both modules, which
        # the equal-literals rule allows, and no call passes one.
        assert _helpers(generated) == []
        assert "return first(rows, text)" in generated
        assert generated.count('"total-"') == 1
        assert all("'" not in call for call in calls)
    assert _same(tmp_path, source)
