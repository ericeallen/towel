"""Execute hostile fixtures before and after fixed-point refactoring.

Each fixture in ``tests/hostile_cases`` is a script whose ``__main__`` block
prints every observation that an extraction could disturb: evaluation order,
evaluation count, conditional evaluation, closure cells, deletion, and
pattern bindings. The battery asserts that the program's output is identical
after refactoring, and records per fixture whether the current engine
transforms it or rejects it, so a change in either direction is visible.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

CASES = Path(__file__).parent / "hostile_cases"

TRANSFORMED = {
    "h04_closure_freevar",
    "h05_cond_return_plus_retvar",
    "h13_dunder_file",
    "h26_match_capture",
    "h39_augassign_after_block",
    "h52_del_after_block",
    "h54_attr_param_property",
    "h57_exception_in_param",
    "r01_property_thunk_order",
    "r02_cluster_attrs",
    "r14_starred_param",
    "r22_fstring_attr",
    "r27_del_subscript",
    "r32_method_callee",
    "r38_name_two_positions",
    "r39_cluster_alpha",
    "r40_class_hierarchy",
    "r44_cluster_nested_overlap",
    "r45_async_no_await",
    "r46_nonlocal_counter",
    "r47_lru_cache_method",
    "r54_with_binding_used_after",
    "r55_chained_assign",
    "r60_local_import",
    "r63_except_var_used_in_handler",
    "r74_starred_vs_plain",
    "r80_inlined_leading_thunk",
    "r81_conditionally_bound_free_variable",
    "r82_local_classes_same_name",
    "r83_tab_indented_class",
    "r84_warn_stacklevel",
}


def _run(script: Path) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, script.name],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]


@pytest.mark.parametrize("case", sorted(path.stem for path in CASES.glob("*.py")))
def test_refactoring_preserves_program_output(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-") as directory:
        root = Path(directory)
        before = root / "before" / "m.py"
        after = root / "after" / "m.py"
        before.parent.mkdir()
        after.parent.mkdir()
        shutil.copy(CASES / f"{case}.py", before)
        shutil.copy(CASES / f"{case}.py", after)
        engine = UnificationRefactorEngine(min_lines=3)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _, applied, _ = engine.refactor_to_fixed_point(str(after))
        transformed = before.read_bytes() != after.read_bytes()
        assert transformed == (applied > 0)
        assert _run(after) == _run(before)
        assert transformed == (case in TRANSFORMED), (
            "rejected" if not transformed else "transformed"
        )
