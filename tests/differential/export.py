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

"""Write a failing differential case as a hostile fixture, ready to commit.

A case whose one module needs neither types nor ``--cross-module`` becomes a
script for ``tests/hostile_cases/``: the module's source, then a
``__main__`` block that runs the case's probes and prints what they show.
Any other case becomes a package for ``tests/hostile_crossfile/``: the
project, and a ``run.py`` that imports its modules and runs the probes.

Each written fixture is checked with the batteries' own procedure
(:mod:`tests.hostile_execution`): refactored as its battery refactors it,
and run before and after. A fixture whose output does not differ is still
written, and its note says so: the difference was one only the
differential observer sees (a signature, a class's ``__dict__``), and the
fixture needs a probe that prints it before it can be committed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import shutil
import tempfile
from typing import List, Literal, Optional, Tuple

from tests.differential.cases import Calls, Case, Value
from tests.differential.runner import Outcome
from tests.hostile_execution import observe
from tests.hostile_refactoring import refactor_package, refactor_script

Battery = Literal["hostile_cases", "hostile_crossfile"]

PROBE_HELPERS = """\
    import copy
    import re

    def _shown(value):
        return re.sub(r"0x[0-9a-fA-F]+", "0xADDR", repr(value))

    def _calls(label, function, argument_sets):
        for arguments in argument_sets:
            arguments = copy.deepcopy(arguments)
            try:
                outcome = "-> " + _shown(function(*arguments))
            except Exception as error:
                outcome = f"raised {type(error).__name__}: {_shown(str(error))}"
            print(label, outcome, "| arguments after:", _shown(arguments))

    def _value(label, produce):
        try:
            print(label, "=", _shown(produce()))
        except Exception as error:
            print(label, "raised", type(error).__name__, _shown(str(error)))
"""
"""The functions a fixture's probes print through, as the ``__main__`` block defines them.

``_calls`` calls a function once per argument tuple, on a deep copy, and
prints the result or the exception with the arguments after the call.
``_value`` prints a value or the exception computing it. Memory addresses
are normalized, since the two runs compared are separate processes.
"""


def probe_lines(case: Case, indent: str = "    ") -> List[str]:
    """The statements that run the case's probes and print what each shows.

    Last come each module's public names, which the observer compares too: a
    name a refactoring adds to a module reaches every importer.
    """
    lines: List[str] = []
    for probe in case.probes:
        if isinstance(probe, Calls):
            lines.append(f"{indent}_calls({probe.target!r}, {probe.target}, {probe.arguments})")
        elif isinstance(probe, Value):
            lines.append(f"{indent}_value({probe.expression!r}, lambda: {probe.expression})")
        else:
            lines += [indent + line for line in probe.source.splitlines()]
    for alias, _ in case.modules:
        public = f"sorted(name for name in dir({alias}) if not name.startswith('_'))"
        lines.append(f"{indent}_value('public names of {alias}', lambda: {public})")
    return lines


@dataclass(frozen=True)
class Fixture:
    """A fixture written for one failing outcome."""

    name: str
    battery: Battery
    path: Path
    reproduced: bool
    settings: Tuple[str, ...]
    """What the battery must be told about it, as the names of its sets."""

    def note(self, outcome: Outcome) -> str:
        """What to do with the fixture, then the full report."""
        target = f"tests/{self.battery}/{self.path.name}"
        lines = [
            f"Fixture {self.name}: copy {self.path} to {target}.",
            (
                "Its output differs before and after refactoring, as its battery runs it."
                if self.reproduced
                else "Its output does NOT differ as its battery runs it: the difference is one"
                " only the differential observer records. Add a probe that prints it before"
                " committing the fixture."
            ),
        ]
        battery_file = (
            "tests/test_hostile_battery.py"
            if self.battery == "hostile_cases"
            else "tests/test_hostile_crossfile_battery.py"
        )
        for setting in self.settings:
            lines.append(f"Add {self.name!r} to {setting} in {battery_file}.")
        lines.append(
            f"Add it to KNOWN_DEFECTS in {battery_file} with the defect's id, and to"
            " TRANSFORMED once the fix lands if the fix still extracts it."
        )
        return "\n".join(lines) + "\n\n" + outcome.report() + "\n"


def fixture_name(outcome: Outcome, battery: Battery, prefix: str) -> str:
    """The fixture's name: ``r<prefix>_grammar_u0898`` for a script, ``xf<prefix>_...`` for a package.

    Fixtures from several branches meet in one directory, so each is named by
    the prefix of the branch that adds it rather than by the next free
    number; ``_cross`` and ``_typed`` say the mode the case failed in.
    """
    mode = outcome.mode
    suffix = ("_cross" if mode.cross_module else "") + ("_typed" if mode.types else "")
    stem = outcome.case.name.removeprefix("gram_")
    return f"{'r' if battery == 'hostile_cases' else 'xf'}{prefix}_grammar_{stem}{suffix}"


def _header(outcome: Outcome, comment: str) -> str:
    """The comment a fixture opens with: where it came from, what it showed, how to rerun it."""
    case, mode = outcome.case, outcome.mode
    how = "--typed" if mode.types else f"--modes {'cross' if mode.cross_module else 'default'}"
    how += f" --forms {'bindings' if case.bindings else 'grammar'}"
    lines = [
        f"Generated by tests.differential.grammar from seed {case.seed}"
        f"{' (typed)' if case.typed else ''}; refactored by towel {mode.label or '(types on)'},"
        f" its behaviour {'changed' if outcome.status == 'different' else outcome.status}.",
        f"Rerun: python -m tests.differential.fuzz --count 1 --seed {case.seed} {how}",
        *comment.splitlines(),
    ]
    return "".join(f"# {line}\n" for line in lines)


def _script(case: Case, header: str) -> str:
    """The case's one module, with a ``__main__`` block that runs its probes."""
    source = next(text for path, text in case.files if path.endswith("m.py"))
    alias = case.modules[0][0]
    return (
        header
        + source.rstrip("\n")
        + '\n\n\nif __name__ == "__main__":\n    import sys\n\n'
        + PROBE_HELPERS
        + f"\n    {alias} = sys.modules[__name__]\n"
        + "\n".join(probe_lines(case))
        + "\n"
    )


def _run_script(case: Case, header: str) -> str:
    """``run.py`` for a package fixture: import each module under its alias, then run the probes."""
    imports = [f"    import {name} as {alias}" for alias, name in case.modules]
    return (
        header
        + 'if __name__ == "__main__":\n'
        + "\n".join(imports)
        + "\n"
        + PROBE_HELPERS
        + "\n"
        + "\n".join(probe_lines(case))
        + "\n"
    )


def _reproduces(fixture: Path, battery: Battery, outcome: Outcome) -> bool:
    """Whether the battery, refactoring ``fixture`` its own way, sees the program's output change."""
    with tempfile.TemporaryDirectory(prefix="towel-export-") as directory:
        before, after = Path(directory).resolve() / "before", Path(directory).resolve() / "after"
        if battery == "hostile_cases":
            for root in (before, after):
                root.mkdir()
                shutil.copy(fixture, root / "m.py")
            refactor_script(after / "m.py")
            return observe("m.py", before) != observe("m.py", after)
        shutil.copytree(fixture, before)
        shutil.copytree(fixture, after)
        refactor_package(
            after / "pkg", cross_module=outcome.mode.cross_module, typed=outcome.mode.types
        )
        return observe("run.py", before) != observe("run.py", after)


def write_fixture(
    outcome: Outcome,
    out: Path,
    *,
    prefix: str = "fz",
    name: Optional[str] = None,
    comment: str = "",
) -> Fixture:
    """Write ``outcome``'s case under ``out`` as the fixture its battery takes, with a note beside it.

    It is named by :func:`fixture_name` with ``prefix``, unless ``name`` is
    given, and opens with a comment saying where it came from, then ``comment``.
    """
    case, mode = outcome.case, outcome.mode
    as_script = case.layout != "multi" and not mode.types and not mode.cross_module
    battery: Battery = "hostile_cases" if as_script else "hostile_crossfile"
    name = name or fixture_name(outcome, battery, prefix)
    settings: List[str] = []
    if as_script:
        path = out / battery / f"{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_script(case, _header(outcome, comment)), encoding="utf-8")
    else:
        path = out / battery / name
        for relative, text in case.files:
            if relative.endswith(".py") and "/" not in relative:
                relative = f"pkg/{relative}"  # a lone module goes into the package the battery runs
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        (path / "pkg" / "__init__.py").touch()
        if case.layout == "single":
            case = _as_package(case)
        (path / "run.py").write_text(_run_script(case, _header(outcome, comment)), encoding="utf-8")
        if not mode.cross_module:
            settings.append("WITHOUT_CROSS_MODULE")
        if mode.types:
            settings.append("TYPED")
    fixture = Fixture(name, battery, path, _reproduces(path, battery, outcome), tuple(settings))
    (out / f"{name}.txt").write_text(fixture.note(outcome), encoding="utf-8")
    return fixture


def _as_package(case: Case) -> Case:
    """``case`` with its lone module moved into the package a package fixture runs."""
    return replace(case, modules=tuple((alias, f"pkg.{name}") for alias, name in case.modules))
