"""An out-of-place run decides exactly what an in-place run on the same project decides.

``towel dry src/pkg OUT`` used to refactor a copy of ``src/pkg`` alone, so the
import-cycle and import-time-effect guards read a tree in which every module
outside the target was missing. A cycle ``pkg.a -> other.c -> pkg.b`` was
invisible, the helper went where it closed that cycle, and the adopted package
could not be imported in any order. The same run in place hosted it where it
was safe. The oracle here is the interpreter: the output is adopted back into a
copy of the project and every module is imported, in several orders, exactly as
the original was.
"""

from __future__ import annotations

import filecmp
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Dict, List, Mapping, NamedTuple, Sequence

import pytest

PYPROJECT = """\
[project]
name = "pkg"
version = "0"
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
where = ["src"]
"""


def _body(name: str, threshold: int, extra: str) -> str:
    return f"""

def f_{name}(items):
    total = 0
    for item in items:
        if item > {threshold}:
            total += item * 2
        else:
            total -= item
    result = total + 7
    return result * 3 + {extra}
"""


class Case(NamedTuple):
    files: Mapping[str, str]
    target: str
    orders: Sequence[str]
    probe: str


CASES: Dict[str, Case] = {
    # pkg.a imports other.c, which imports pkg.b: a helper hosted in pkg.a and
    # imported by pkg.b closes pkg.b -> pkg.a -> other.c -> pkg.b.
    "sibling_package_cycle": Case(
        {
            "pyproject.toml": PYPROJECT,
            "src/pkg/__init__.py": "",
            "src/other/__init__.py": "",
            "src/other/c.py": "from pkg.b import Kb\n\nCC = Kb + 1\n",
            "src/pkg/a.py": "from other.c import CC\nKa = 10\n" + _body("a", 4, "CC"),
            "src/pkg/b.py": "Kb = 10\n" + _body("b", 5, "0"),
        },
        "src/pkg",
        ("pkg.a pkg.b", "pkg.b pkg.a", "other.c pkg.a", "other.c pkg.b"),
        "from pkg import a, b; print(a.f_a([1, 5, 9]), b.f_b([1, 5, 9]))",
    ),
    # The target is a subpackage; the cycle runs through its parent package.
    "parent_package_cycle": Case(
        {
            "pyproject.toml": PYPROJECT,
            "src/pkg/__init__.py": "",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/other.py": "from pkg.sub.b import Kb\n\nCC = Kb + 1\n",
            "src/pkg/sub/a.py": "from pkg.other import CC\nKa = 10\n" + _body("a", 4, "CC"),
            "src/pkg/sub/b.py": "Kb = 10\n" + _body("b", 5, "0"),
        },
        "src/pkg/sub",
        ("pkg.sub.a pkg.sub.b", "pkg.sub.b pkg.sub.a", "pkg.other"),
        "from pkg.sub import a, b; print(a.f_a([1, 5, 9]), b.f_b([1, 5, 9]))",
    ),
    # Importing the helper's host runs a sibling package's import-time effect,
    # which importing the borrower never used to.
    "sibling_import_effect": Case(
        {
            "pyproject.toml": PYPROJECT,
            "src/pkg/__init__.py": "",
            "src/other/__init__.py": "",
            "src/other/noisy.py": "print('NOISY IMPORTED')\nN = 1\n",
            "src/pkg/a.py": "from other.noisy import N\nKa = 10\n" + _body("a", 4, "N"),
            "src/pkg/b.py": "Kb = 10\n" + _body("b", 5, "0"),
        },
        "src/pkg",
        ("pkg.b", "pkg.a pkg.b", "pkg.b pkg.a"),
        "from pkg import b; print(b.f_b([1, 5, 9]))",
    ),
}


def _write(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _dry(target: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(target),
            str(output),
            "--no-interactive",
            "--progress",
            "none",
            "--no-types",
            "--no-format",
            "--cross-module",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


def _imported(source_root: Path, orders: Sequence[str], probe: str) -> List[str]:
    """What importing each order of modules prints, error included; the runtime oracle."""
    seen = []
    for order in orders:
        program = f"import importlib\nfor m in {order.split()!r}: importlib.import_module(m)\n"
        ran = subprocess.run(
            [sys.executable, "-c", program + probe],
            capture_output=True,
            text=True,
            cwd=source_root,
            timeout=120,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
        )
        seen.append(f"rc={ran.returncode}\n{ran.stdout}{ran.stderr.strip().splitlines()[-1:]}")
    return seen


def _trees_equal(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right)
    pending = [comparison]
    while pending:
        current = pending.pop()
        if current.left_only or current.right_only or current.funny_files:
            return False
        _, mismatch, errors = filecmp.cmpfiles(
            current.left, current.right, current.common_files, shallow=False
        )
        if mismatch or errors:
            return False
        pending.extend(current.subdirs.values())
    return True


@pytest.mark.parametrize("name", sorted(CASES))
def test_out_of_place_output_adopts_as_the_in_place_run_does(tmp_path: Path, name: str) -> None:
    case = CASES[name]
    original = tmp_path / "original"
    _write(original, case.files)
    expected = _imported(original / "src", case.orders, case.probe)

    in_place = tmp_path / "in_place"
    shutil.copytree(original, in_place)
    ran = _dry(in_place / case.target, in_place / case.target)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Applied" in ran.stdout, "the case must exercise a refactoring: " + ran.stdout

    adopted = tmp_path / "adopted"
    shutil.copytree(original, adopted)
    output = tmp_path / "output"
    ran = _dry(adopted / case.target, output)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    # The project was only read.
    assert _trees_equal(original, adopted)
    shutil.rmtree(adopted / case.target)
    shutil.copytree(output, adopted / case.target)
    assert _imported(adopted / "src", case.orders, case.probe) == expected
    # Only the target is published, and it is byte for byte what the in-place run wrote.
    assert not (output / "pyproject.toml").exists()
    assert _trees_equal(in_place / case.target, output)


def test_every_reported_path_names_the_output_not_the_stage(tmp_path: Path) -> None:
    case = CASES["sibling_package_cycle"]
    project = tmp_path / "project"
    _write(project, case.files)
    output = tmp_path / "output"
    ran = _dry(project / case.target, output)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "towel-stage" not in ran.stdout + ran.stderr
    assert f"{output / 'b.py'}: 1 refactoring(s)" in ran.stdout, ran.stdout
    sidecar = json.loads((output / ".towel-helpers.json").read_text(encoding="utf-8"))
    files = {site["file"] for sites in sidecar["helpers"].values() for site in sites}
    assert files == {"a.py", "b.py"}


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_a_checked_run_out_of_place_accepts_what_the_run_in_place_accepts(tmp_path: Path) -> None:
    """The stage outside the target is the original; the checker must not be shown it again.

    Restated to mypy, a module the configuration leaves out is checked after
    all, its errors read as new ones, and every proposal was refused.
    """
    original = tmp_path / "original"
    _write(
        original,
        {
            "pyproject.toml": '[tool.mypy]\nfiles = ["pkg"]\n',
            "pkg/__init__.py": "",
            **{f"pkg/{name}.py": f"""class {name.capitalize()}:
    def __init__(self) -> None:
        self.tag = "{name[0]}"

    def render(self, width: int) -> str:
        body = str(self.tag).strip()
        padded = body.rjust(width, ".")
        return padded.upper()
""" for name in ("alpha", "beta")},
            "unchecked.py": 'broken: int = "not checked by this project"\n',
        },
    )
    in_place = tmp_path / "in_place"
    shutil.copytree(original, in_place)
    typed = [
        "--no-interactive",
        "--progress",
        "none",
        "--no-format",
        "--cross-module",
        "--min-lines",
        "3",
    ]
    command = [sys.executable, "-m", "towel.cli", "dry"]
    ran = subprocess.run(
        [*command, str(in_place / "pkg"), str(in_place / "pkg"), *typed],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert ran.returncode == 0 and "Applied" in ran.stdout, ran.stdout + ran.stderr
    output = tmp_path / "output"
    ran = subprocess.run(
        [*command, str(original / "pkg"), str(output), *typed],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Dropped a proposal" not in ran.stderr, ran.stderr
    assert _trees_equal(in_place / "pkg", output)
