"""pyright's language server is configured as its command line configures itself.

Towel judges candidates through a language server and confirms a run with the
command line, so the two must reach one verdict on the same files. They share
an analyzer but not its configuration: where the client is silent, the server
has defaults of its own. ``autoSearchPaths`` was the one that decided
verdicts. The command line always sets it; the server sets it only for a
client that sends no ``python.analysis`` section, and Towel sends one. In a
``src`` layout the package was then analyzed as ``src.<package>``, and
``import <package>`` resolved to the environment's installed copy -- with an
editable install, the user's own tree, not the copy under check. A candidate
that removed ``edit`` from click's exports broke ``tests/typing/typing_edit.py``
in six places by the command line and in none by the server, and trio's
modules clashed with themselves: ``"src.trio._core._run.Task" is not assignable
to "trio._core._run.Task"``.

The command line's probes had a divergence of their own: pyright found their
project from its working directory, which it sees with symbolic links
resolved, so where the temporary directory is a link, as on macOS, a probed
module was outside the project found for it and clashed with its own package.

Each project here is checked in an environment of its own, as a user's is:
an untyped library installed, and the project either installed editable, as
its developers have it and as the corpus runs it, or not installed at all.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import List, Mapping, Tuple

import pytest

from towel.pyright_session import server_settings
from towel.type_inference import (
    CheckResult,
    CheckSuccess,
    PyrightOracle,
    RevealRequest,
    served_by_a_language_server,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

PACKAGE = {
    "src/pkg/__init__.py": "from ._termui import edit as edit\n",
    "src/pkg/_termui.py": "def edit(text: str | None = None) -> str | None:\n    return text\n",
    # The consumer outside the package, as click's typing tests are.
    "tests/typing/typing_edit.py": (
        "from typing import assert_type\n\nimport pkg\n\n"
        'assert_type(pkg.edit("x"), str | None)\n'
    ),
}

CLICK = '[project]\nname = "pkg"\nversion = "1.0"\n\n[tool.pyright]\n' + (
    'include = ["src", "tests/typing"]\ntypeCheckingMode = "basic"\n'
)
"""click's own pyright configuration, but for its Python version."""


def _write(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _environment(tmp_path: Path, project: Path, *, editable: bool) -> Path:
    """An environment's interpreter: an untyped library in it, and the project if ``editable``.

    ``pip install -e`` of a setuptools project leaves a ``.pth`` naming the
    source root, which is all pyright reads of an install.
    """
    environment = tmp_path / "environment"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        check=True,
        capture_output=True,
    )
    (site,) = (environment / "lib").glob("python*/site-packages")
    _write(site, {"untyped_lib/__init__.py": "def number():\n    return 1\n"})
    if editable:
        (site / "_pkg_editable.pth").write_text(f"{project / 'src'}\n", encoding="utf-8")
    return environment / "bin" / "python"


def _oracle(interpreter: Path, *, language_server: bool) -> PyrightOracle:
    """An oracle checking in the environment of ``interpreter``, as Towel installed there does."""
    oracle = PyrightOracle(language_server=language_server)
    oracle._interpreter = str(interpreter)
    return oracle


Errors = List[Tuple[str, int, str]]


def _errors(result: CheckResult, project: Path) -> Errors:
    assert isinstance(result, CheckSuccess), result
    return sorted(
        (Path(error.path).relative_to(project).as_posix(), error.line or 0, error.message)
        for error in result.errors
    )


def _without_the_edit_export(init: Path) -> str:
    text = init.read_text(encoding="utf-8")
    candidate = "".join(line for line in text.splitlines(True) if "import edit as edit" not in line)
    assert candidate != text
    return candidate


def _baseline_and_candidate(
    project: Path, interpreter: Path, *, language_server: bool
) -> Tuple[Errors, Errors]:
    init = project / "src/pkg/__init__.py"
    oracle = _oracle(interpreter, language_server=language_server)
    try:
        baseline = oracle.check_project({str(init): init.read_text(encoding="utf-8")})
        candidate = oracle.check_project({str(init): _without_the_edit_export(init)})
        assert served_by_a_language_server(oracle) is language_server
    finally:
        oracle.close()
    return _errors(baseline, project), _errors(candidate, project)


def test_the_server_is_sent_the_command_lines_settings() -> None:
    """The whole set, so a setting added or dropped is a decision someone reads about.

    ``server_settings`` says, setting by setting, why each is sent or left unset.
    """
    assert server_settings("/env/bin/python") == {
        "python": {
            "pythonPath": "/env/bin/python",
            "analysis": {
                "diagnosticMode": "workspace",
                "autoSearchPaths": True,
                "typeCheckingMode": "standard",
                "useLibraryCodeForTypes": True,
            },
        }
    }


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
@pytest.mark.parametrize("editable", [True, False], ids=["installed-editable", "not-installed"])
def test_a_consumer_outside_src_is_judged_against_the_candidate(
    tmp_path: Path, language_server: bool, editable: bool
) -> None:
    """click's case: removing an export breaks a typing test outside the package.

    Towel verifies a candidate by checking the prospective project, and a
    candidate the check calls clean is applied. Through the server, the
    consumer resolved ``pkg`` to the installed tree, which still exports
    ``edit``, and the candidate was clean; with nothing installed, ``pkg``
    did not resolve at all and the untouched project was already refused.
    """
    project = tmp_path / "project"
    _write(project, {**PACKAGE, "pyproject.toml": CLICK})
    interpreter = _environment(tmp_path, project, editable=editable)
    baseline, candidate = _baseline_and_candidate(
        project, interpreter, language_server=language_server
    )
    assert baseline == [], "the project as it stands checks clean"
    broken = [error for error in candidate if error[0] == "tests/typing/typing_edit.py"]
    assert any('"edit" is not a known attribute of module "pkg"' in e[2] for e in broken), candidate
    init = project / "src/pkg/__init__.py"
    assert init.read_text(encoding="utf-8") == PACKAGE["src/pkg/__init__.py"], "the project changed"


AGREEMENT = {
    **PACKAGE,
    # A module reaching its own package both ways, as trio's do: two copies clash here.
    "src/pkg/_core.py": "class Task:\n    pass\n",
    "src/pkg/_run.py": (
        "from pkg._core import Task\nfrom ._core import Task as Local\n\n\n"
        "def make() -> Task:\n    task: Local = Local()\n    return task\n"
    ),
    # An error in strict mode only.
    "tests/test_loose.py": "def loose(value):\n    return value\n",
    # An error in standard and strict modes, not in basic or off.
    "tests/test_override.py": (
        "class Base:\n    def run(self, value: int) -> int:\n        return value\n\n\n"
        "class Child(Base):\n    def run(self, value: str) -> int:\n        return 0\n"
    ),
    # An error only where library code is read for types.
    "tests/test_library.py": "from untyped_lib import number\n\nvalue: str = number()\n",
    # Found only through the configuration's own search paths.
    "lib/helper/__init__.py": "def assist() -> int:\n    return 1\n",
    "tests/test_helper.py": "from helper import assist\n\nvalue: str = assist()\n",
}

CONFIGURATIONS = {
    # The server applies its client's settings only here, so each one sent shows.
    "none": {"pyproject.toml": '[project]\nname = "pkg"\nversion = "1.0"\n'},
    "click": {"pyproject.toml": CLICK.replace('"tests/typing"', '"tests"')},
    "strict": {"pyrightconfig.json": '{"typeCheckingMode": "strict"}'},
    "no-library-code": {"pyrightconfig.json": '{"useLibraryCodeForTypes": false}'},
    # Search paths the configuration names replace the ``src`` default in both.
    "extra-paths": {"pyrightconfig.json": '{"extraPaths": ["lib", "src"]}'},
}


@pytest.mark.parametrize("editable", [True, False], ids=["installed-editable", "not-installed"])
@pytest.mark.parametrize("configuration", sorted(CONFIGURATIONS))
def test_the_server_and_the_command_line_agree_on_a_src_layout(
    tmp_path: Path, configuration: str, editable: bool
) -> None:
    """One verdict from both paths, before and after a candidate, whatever configures the check.

    The project holds an error for each setting the server is sent that a
    wrong value would show: a strict-only error, a standard-only error, one
    that needs library code for its types, and the consumer outside ``src``.
    """
    project = tmp_path / "project"
    _write(project, {**AGREEMENT, **CONFIGURATIONS[configuration]})
    interpreter = _environment(tmp_path, project, editable=editable)
    served = _baseline_and_candidate(project, interpreter, language_server=True)
    command_line = _baseline_and_candidate(project, interpreter, language_server=False)
    assert served == command_line
    baseline, candidate = served
    assert not [error for error in baseline if "src.pkg" in error[2]], baseline
    assert any(error[0] == "tests/typing/typing_edit.py" for error in candidate), candidate


PROBED = {
    "pkg/__init__.py": "",
    "pkg/_core.py": "class Task:\n    pass\n",
    "pkg/use.py": (
        "from pkg._core import Task\nfrom ._core import Task as Local\n\n\n"
        "def make() -> Task:\n    task = Local()\n    return task\n"
    ),
}


@pytest.mark.parametrize(
    "layout",
    [
        {"pyproject.toml": '[tool.pyright]\ntypeCheckingMode = "basic"\n', **PROBED},
        {
            "setup.py": "from setuptools import setup\n\nsetup()\n",
            **{f"src/{name}": text for name, text in PROBED.items()},
        },
    ],
    ids=["flat-configured", "src-without-configuration"],
)
def test_a_probe_is_answered_alike_by_the_server_and_the_command_line(
    tmp_path: Path, layout: Mapping[str, str]
) -> None:
    """Inference and subtyping, asked of both paths, in a module reaching its package both ways.

    The command line's probe was answered where pyright found the project
    from its working directory: past a symbolically linked temporary
    directory in the flat layout, and at the probe's own directory in the
    ``src`` one, where ``src`` is not found. Either way the module's two
    names for ``Task`` were two classes, and ``Local`` was not a ``Task``.
    """
    project = tmp_path / "project"
    _write(project, layout)
    interpreter = _environment(tmp_path, project, editable="setup.py" in layout)
    use = next(project.rglob("use.py"))
    source = use.read_text(encoding="utf-8")
    request = RevealRequest(str(use), source, 7, "    ", ("task", "Local()"))
    answers = []
    for language_server in (True, False):
        oracle = _oracle(interpreter, language_server=language_server)
        try:
            revealed = oracle.reveal([request])
            subtyping = oracle.is_subtype(str(use), source, [("Local", "Task"), ("Task", "Local")])
        finally:
            oracle.close()
        answers.append((sorted(revealed.items()), [verdict.value for verdict in subtyping]))
    served, command_line = answers
    assert served == command_line
    assert served[1] == ["yes", "yes"]
    assert [kind for _, kind in served[0]] == ["Task", "Task"]


SOURCE = textwrap.dedent("""
    def first(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 3
        return answer


    def second(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 4
        return answer
    """).lstrip()


def test_a_src_layout_project_is_refactored_and_confirmed(tmp_path: Path) -> None:
    """A run over a ``src`` layout whose tests import the package, in Towel's own environment.

    The server could not resolve the package from the tests, so the original
    project was refused as having type errors it does not have. Now the run
    applies, every candidate is judged against its consumers, and the cold
    confirmation of the finished project agrees.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "pkg"\nversion = "1.0"\n\n[tool.pyright]\n'
                'typeCheckingMode = "basic"\n'
            ),
            "src/pkg/__init__.py": "",
            "src/pkg/numbers.py": SOURCE,
            "tests/test_numbers.py": (
                "from pkg.numbers import first, second\n\n"
                "values: tuple[int, int] = (first(1), second(2))\n"
            ),
        },
    )
    module = tmp_path / "src/pkg/numbers.py"
    oracle = PyrightOracle()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        _, applied, _ = engine.refactor_to_fixed_point(str(module), progress="none")
        assert served_by_a_language_server(oracle), "the run was not checked by a server"
    finally:
        oracle.close()
    assert applied >= 1
    assert "_extracted_func" in module.read_text(encoding="utf-8")
