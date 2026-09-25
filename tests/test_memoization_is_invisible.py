"""Memoization changes no verdict: an analysis concludes the same with every memo on or off.

A memo is sound only for a function pure in its key. The round-3 audit's
P1-1 was a key that left out what its answer depended on: a verdict about a
block, keyed by the structure of the block and its function, answered for
the same code in another context. Whichever cache a future key drops a
dependency from, an analysis under ``memoization_disabled`` computes that
answer afresh and so disagrees with the memoized one somewhere below.

One engine analyzes each case in turn, as a fixed-point run analyzes file
after file, so a memo can carry over from one analysis to the next. The
verdicts compared are the reason each candidate pair was declined, in the
order the pairs were judged, and every proposal. The cases are the hostile
fixtures, and generated modules that put one block in many contexts: in a
loop and at the top of its function, after other bindings, twice in one
function, nested in a function whose enclosing one rebinds what it reads or
does not, in a method, in a module that reflects on its namespace, and split
across two modules.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Hashable, Iterator, List, Sequence, Tuple

from towel.diagnostics import REJECTIONS
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.models import proposal_identity
from towel.unification.refactor_engine import UnificationRefactorEngine

HOSTILE = Path(__file__).parent / "hostile_cases"

BLOCKS: Dict[str, str] = {
    "reads_after_callback": 'print("start", x)\ncb()\nprint("after", x)\nprint("end")',
    "binds_and_reads": 'w = len(items)\nprint("w", w)\nprint("x")',
    "binds_and_deletes": 'v = len(items)\nprint("v", v)\ndel v',
    "private_parameter": 'y = len(items)\nz = (lambda __p=0: 7)(__p=y)\nprint("z", y, z)',
}

# Each context holds the block where ``{block}`` stands, at its indentation.
CONTEXTS: Dict[str, str] = {
    "top": "def {name}(x, items, cb):\n{block}",
    "loop": (
        "def {name}(x, items, cb):\n    w = v = 0\n    for _ in range(2):\n"
        '        print("before", w, v)\n{block}'
    ),
    "branch": 'def {name}(x, items, cb):\n    if items:\n{block}\n    print("done")',
    "after_bindings": (
        'def {name}(x, items, cb):\n    w = v = y = z = 1\n{block}\n    print("again", w, v)'
    ),
    "nested": "def {name}(items, cb):\n    x = 0\n\n    def inner():\n{block}\n\n    inner()",
    "nested_rebound": (
        "def {name}(items):\n    x = 0\n\n    def cb():\n        nonlocal x\n        x += 1\n\n"
        "    def inner():\n{block}\n\n    inner()"
    ),
    "method": "class {title}:\n    def m(self, x, items, cb):\n{block}",
    "method_with_receiver": (
        "class {title}:\n    def m(self, x, items, cb):\n        print(self)\n{block}"
    ),
    "global_rebound": "def {name}(items, cb):\n    global x\n    x = len(items)\n{block}",
    # The block twice in one function: at its top, and in a loop after it.
    "twice": (
        "def {name}(x, items, cb):\n{top}\n    for _ in range(2):\n"
        '        print("before", w, v)\n{block}'
    ),
}

# Where each context's block stands.
_DEPTH = {"top": 4, "after_bindings": 4, "global_rebound": 4}


def _function(context: str, block: str, index: int) -> str:
    depth = _DEPTH.get(context, 8)
    return CONTEXTS[context].format(
        name=f"{context}_{index}",
        title=f"{context.title().replace('_', '')}{index}",
        block=textwrap.indent(block, " " * depth),
        top=textwrap.indent(block, "    "),
    )


def _module(block: str, contexts: Sequence[str], *, reflective: bool = False) -> str:
    functions = [_function(context, block, index) for index, context in enumerate(contexts)]
    if reflective:
        functions.append("def reflect():\n    return globals()")
    return "x = 0\n\n\n" + "\n\n\n".join(functions) + "\n"


@dataclass(frozen=True)
class _Case:
    """Files analyzed together, as one directory analysis would."""

    name: str
    files: Tuple[Tuple[str, str], ...]


# Each module pairs a context that lets the block move with ones that may not.
_MODULES = (
    ("positions", ("top", "twice", "loop", "branch", "after_bindings")),
    ("scopes", ("top", "nested", "nested_rebound", "global_rebound")),
    ("classes", ("top", "method", "method_with_receiver")),
)


def _generated() -> Iterator[_Case]:
    """Every block in every context, a few contexts to a module; in a reflective one; split in two."""
    for label, block in BLOCKS.items():
        for kind, contexts in _MODULES:
            yield _Case(f"{label}-{kind}", (("m.py", _module(block, contexts)),))
        yield _Case(
            f"{label}-reflective", (("m.py", _module(block, ("top", "nested"), reflective=True)),)
        )
        yield _Case(
            f"{label}-split",
            (
                ("a.py", _module(block, ("top", "nested"))),
                ("b.py", _module(block, ("loop", "nested_rebound", "method_with_receiver"))),
            ),
        )


def _hostile() -> Iterator[_Case]:
    for path in sorted(HOSTILE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError:
            continue  # syntax this Python does not have
        yield _Case(path.stem, ((path.name, source),))


class _Trace(logging.Handler):
    """Collects the rejection trace: one line per declined pair, naming it and why."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def _traced() -> Iterator[_Trace]:
    trace = _Trace()
    level, propagate = REJECTIONS.level, REJECTIONS.propagate
    REJECTIONS.addHandler(trace)
    REJECTIONS.setLevel(logging.DEBUG)
    REJECTIONS.propagate = False
    try:
        yield trace
    finally:
        REJECTIONS.removeHandler(trace)
        REJECTIONS.setLevel(level)
        REJECTIONS.propagate = propagate


Verdicts = Tuple[Tuple[str, ...], Tuple[Hashable, ...]]


def _written(root: Path, cases: Sequence[_Case]) -> Dict[str, List[str]]:
    """Each case's files, written under ``root``: their paths, by case."""
    paths: Dict[str, List[str]] = {}
    for case in cases:
        directory = root / case.name
        directory.mkdir(parents=True)
        for name, source in case.files:
            (directory / name).write_text(source, encoding="utf-8")
        paths[case.name] = [str(directory / name) for name, _ in case.files]
    return paths


def _verdicts(
    paths: Dict[str, List[str]], *, memoized: bool, cross_module: bool = False
) -> Dict[str, Verdicts]:
    """What one engine concludes about each case in turn, with memos on or off."""
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=cross_module)
    found: Dict[str, Verdicts] = {}
    with contextlib.ExitStack() as stack:
        if not memoized:
            stack.enter_context(memoization_disabled())
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        for name, files in paths.items():
            with _traced() as trace:
                proposals = engine.analyze_files(files, progress="none")
            found[name] = (
                tuple(trace.lines),
                tuple(proposal_identity(proposal) for proposal in proposals),
            )
    return found


def _assert_alike(tmp_path: Path, cases: Sequence[_Case], *, cross_module: bool = False) -> None:
    # Analysis only reads the files, so both runs read the same ones.
    paths = _written(tmp_path, cases)
    memoized = _verdicts(paths, memoized=True, cross_module=cross_module)
    fresh = _verdicts(paths, memoized=False, cross_module=cross_module)
    differing = [name for name in paths if memoized[name] != fresh[name]]
    assert not differing, f"memoization changed the verdicts of {differing}"
    assert any(trace or proposals for trace, proposals in memoized.values())


def test_memoization_changes_no_verdict_on_the_hostile_fixtures(tmp_path: Path) -> None:
    _assert_alike(tmp_path, list(_hostile()))


def test_memoization_changes_no_verdict_on_blocks_in_many_contexts(tmp_path: Path) -> None:
    cases = list(_generated())
    for case in cases:
        for _, source in case.files:
            ast.parse(source)  # every generated module is Python
    _assert_alike(tmp_path, cases)


def test_memoization_changes_no_verdict_on_pairs_across_modules(tmp_path: Path) -> None:
    split = [case for case in _generated() if len(case.files) > 1]
    _assert_alike(tmp_path, split, cross_module=True)


def test_memoization_changes_nothing_a_fixed_point_run_writes(tmp_path: Path) -> None:
    # Applying a proposal rewrites a file and the next iteration re-analyzes
    # it: a memo that survives the rewrite must still be the rewritten
    # file's answer.
    written: Dict[bool, Dict[str, str]] = {}
    for memoized in (True, False):
        root = tmp_path / ("memoized" if memoized else "fresh")
        for case in _generated():
            if not case.name.endswith("-split"):
                continue
            package = root / case.name
            package.mkdir(parents=True)
            for name, source in case.files:
                (package / name).write_text(source, encoding="utf-8")
        engine = UnificationRefactorEngine(min_lines=3)
        with contextlib.ExitStack() as stack:
            if not memoized:
                stack.enter_context(memoization_disabled())
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
        written[memoized] = {
            str(path.relative_to(root)): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.py"))
        }
        shutil.rmtree(root)
    assert written[True] == written[False]
    assert any("__extracted_func_" in source for source in written[True].values())
