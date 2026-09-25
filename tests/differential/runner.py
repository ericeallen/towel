"""Run Towel on a case and compare the program's behaviour before and after.

Towel runs in this process through its library entry, exactly as the hostile
batteries run it: ``UnificationRefactorEngine(min_lines=3)``, in place on a
copy, ``refactor_to_fixed_point`` for a single file and
``refactor_directory_to_fixed_point`` for a project. ``--no-types`` is
``annotate_helpers=False`` with no type oracle, as ``towel dry`` builds it;
the typed mode takes the checker the project configures, as ``towel dry``
does by default. Evaluation is serial (``TOWEL_WORKERS=1``), so a case runs
the same whatever the machine.

The original and the refactored program are each observed in a fresh
interpreter, the one running this code (:mod:`tests.differential.observer`),
in the emptied environment the batteries use.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import difflib
import io
import json
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Any, Dict, List, Literal, Optional, Tuple

from tests.differential.cases import Case
from tests.differential.comparison import Difference, behaviour_changes
from tests.hostile_execution import ISOLATED_ENV
from towel.diagnostics import Settings
from towel.type_inference import CheckerNotInstalled, TypeOracle, type_oracle_for_project
from towel.unification.refactor_engine import UnificationRefactorEngine

OBSERVER = Path(__file__).with_name("observer.py")
OBSERVER_ENV = {**ISOLATED_ENV, "PYTHONHASHSEED": "0"}
"""The batteries' emptied environment, with string hashing fixed so set order cannot differ."""
OBSERVER_SECONDS = 60
"""How long an observation may take in all; each probe call has its own, shorter limit."""

Record = Dict[str, Any]

Status = Literal["unchanged", "equivalent", "different", "failed", "inconclusive", "unsupported"]
"""What running a case showed.

``unchanged``: Towel extracted nothing. ``equivalent``: it extracted, and no
behaviour changed. ``different``: a behaviour changed. ``failed``: Towel
raised, or wrote code that does not compile, or said it applied nothing
while changing files. ``inconclusive``: the original itself did not finish,
so there is nothing to compare. ``unsupported``: this Python cannot run the
case, or the checker a typed case configures is not installed.
"""


@dataclass(frozen=True)
class Mode:
    """How Towel is run: with or without ``--cross-module``, and typed or ``--no-types``."""

    cross_module: bool = False
    types: bool = False

    @property
    def label(self) -> str:
        """The mode as ``towel dry`` flags."""
        flags = ["--cross-module"] if self.cross_module else []
        return " ".join(flags + ([] if self.types else ["--no-types"]))


DEFAULT = Mode()
CROSS_MODULE = Mode(cross_module=True)


@dataclass(frozen=True)
class Outcome:
    """What running one case in one mode showed, with what a person needs to act on it."""

    case: Case
    mode: Mode
    status: Status
    detail: str = ""
    applied: int = 0
    refactoring: str = ""
    changes: Tuple[Difference, ...] = ()
    towel_output: str = ""

    @property
    def is_defect(self) -> bool:
        """Whether this outcome shows Towel doing something wrong."""
        return self.status in ("different", "failed")

    def report(self) -> str:
        """A failure message: the seed, the generated source, Towel's change and each difference."""
        case = self.case
        how = (
            "--typed"
            if self.mode.types
            else f"--modes {'cross' if self.mode.cross_module else 'default'}"
        )
        lines = [
            f"{case.name} (seed {case.seed}{', typed' if case.typed else ''}, {case.layout} layout),"
            f" towel {self.mode.label or '(types on)'}: {self.status}. {self.detail}".rstrip(),
            f"Reproduce: python -m tests.differential.fuzz --count 1 --seed {case.seed} {how}",
            "",
            "--- generated source ---",
        ]
        for path, text in case.files:
            if path.endswith(".py"):
                lines += [f"=== {path}", text.rstrip("\n"), ""]
        if self.refactoring:
            lines += ["--- Towel's change ---", self.refactoring.rstrip("\n"), ""]
        if self.changes:
            lines.append(f"--- {len(self.changes)} change(s) of behaviour ---")
            lines += [change.render() for change in self.changes[:40]]
        if self.status == "failed" and self.towel_output:
            lines += ["--- Towel's output ---", self.towel_output[-4000:]]
        return "\n".join(lines)


def _write(root: Path, case: Case) -> None:
    for relative, text in case.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _python_sources(root: Path) -> Dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _unified(before: Dict[str, str], after: Dict[str, str]) -> str:
    return "".join(
        "".join(
            difflib.unified_diff(
                before.get(name, "").splitlines(True),
                after.get(name, "").splitlines(True),
                f"a/{name}",
                f"b/{name}",
            )
        )
        for name in sorted(set(before) | set(after))
        if before.get(name) != after.get(name)
    )


def _uncompilable(sources: Dict[str, str]) -> Optional[str]:
    """The first file of ``sources`` this Python cannot compile, with the error, or None."""
    for name, text in sources.items():
        try:
            compile(text, name, "exec", dont_inherit=True)
        except SyntaxError as error:
            return f"{name}: {error}"
    return None


def _refactor(case: Case, mode: Mode, root: Path, oracle: Optional[TypeOracle]) -> int:
    """Run Towel on the case written at ``root``, in place, and return how many changes it applied."""
    engine = UnificationRefactorEngine(
        min_lines=3,
        cross_module_helpers=mode.cross_module,
        annotate_helpers=mode.types,
        type_oracle=oracle,
        settings=Settings.from_environ({"TOWEL_WORKERS": "1"}),
    )
    single = case.refactored_file
    if single is not None:
        _, applied, _ = engine.refactor_to_fixed_point(str(root / single), progress="none")
        return applied
    results, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    return sum(count for count, _ in results.values())


def _observe(case: Case, roots: Tuple[Path, ...], work: Path, call_seconds: float) -> List[Record]:
    """The observer's record of the program at each of ``roots``, observed side by side.

    A record is ``{"observer_timeout": True}`` where the observer did not
    finish; an observer that fails outright is a fault of this harness, and
    raises.
    """
    spec = work / "probes.json"
    spec.write_text(json.dumps({**case.probe_spec(), "call_seconds": call_seconds}), "utf-8")
    outs = [work / f"{root.name}.observed.json" for root in roots]
    running = [
        subprocess.Popen(
            [sys.executable, "-P", str(OBSERVER), str(root), str(spec), str(out)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=OBSERVER_ENV,
        )
        for root, out in zip(roots, outs)
    ]
    records: List[Record] = []
    for root, out, process in zip(roots, outs, running):
        try:
            _, stderr = process.communicate(timeout=OBSERVER_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            records.append({"observer_timeout": True})
            continue
        if process.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"the observer failed on {root} (exit {process.returncode}):\n{stderr}"
            )
        records.append(json.loads(out.read_text("utf-8")))
    return records


def run_case(case: Case, mode: Mode, workspace: Path, *, call_seconds: float = 2.0) -> Outcome:
    """Refactor ``case`` in ``mode`` inside ``workspace`` and compare its behaviour before and after."""
    work = workspace.resolve()
    before, after = work / "before", work / "after"
    for root in (before, after):
        _write(root, case)
    original = _python_sources(before)
    unsupported = _uncompilable(original)
    if unsupported is not None:
        return Outcome(case, mode, "unsupported", f"This Python cannot compile {unsupported}")
    oracle: Optional[TypeOracle] = None
    if mode.types:
        try:
            choice = type_oracle_for_project(after)
        except CheckerNotInstalled as missing:
            return Outcome(
                case, mode, "unsupported", f"The configured checker is absent: {missing}"
            )
        if choice.tool is None:
            return Outcome(case, mode, "unsupported", f"No type checker: {choice.note}")
        oracle = choice.tool
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            applied = _refactor(case, mode, after, oracle)
    except Exception:  # noqa: BLE001 - a crash is the finding, reported with its traceback
        return Outcome(
            case,
            mode,
            "failed",
            "Towel raised.",
            towel_output=output.getvalue() + traceback.format_exc(),
        )
    finally:
        if oracle is not None:
            oracle.close()
    refactored = _python_sources(after)
    change = _unified(original, refactored)
    towel_output = output.getvalue()
    if (applied > 0) != bool(change):
        detail = f"Towel reported {applied} change(s) applied, and the files say otherwise."
        return Outcome(case, mode, "failed", detail, applied, change, towel_output=towel_output)
    if not change:
        return Outcome(case, mode, "unchanged", towel_output=towel_output)
    broken = _uncompilable(
        {name: refactored[name] for name in refactored if refactored[name] != original.get(name)}
    )
    if broken is not None:
        detail = f"The refactored code does not compile: {broken}"
        return Outcome(case, mode, "failed", detail, applied, change, towel_output=towel_output)
    observed_before, observed_after = _observe(case, (before, after), work, call_seconds)
    if observed_before.get("observer_timeout") or observed_before.get("timed_out"):
        detail = "The original program does not finish its probes."
        return Outcome(
            case, mode, "inconclusive", detail, applied, change, towel_output=towel_output
        )
    changes = behaviour_changes(observed_before, observed_after, cross_module=mode.cross_module)
    return Outcome(
        case,
        mode,
        "different" if changes else "equivalent",
        applied=applied,
        refactoring=change,
        changes=changes,
        towel_output=towel_output,
    )
