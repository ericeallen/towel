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

"""Relaxed mode still keeps alive a local that owns a resource the program goes on using.

Audit finding 1's remedy drops the names 1.949 returned only to keep their
objects alive. For nearly every object that changes only when a finalizer
runs; for a temporary directory it deletes files the caller is about to
read. docs/design/DECISIONS.md, "Relaxed mode is the default; strict mode keeps
exact lifetimes", keeps a guard for a closed list of standard-library
constructors (``towel.unification.resource_guard``). These tests pin the
list's resolution through imports, the returned name, and the guard's
silence everywhere else.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import subprocess
import sys
from typing import Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.resource_guard import (
    RESOURCE_CONSTRUCTORS,
    WEAK_REFERENCE_CONSTRUCTORS,
    resource_bound_names,
)

# The default mode is what this module pins.
pytestmark = pytest.mark.relaxed_mode


def _names(source: str) -> set[str]:
    """What the guard finds in the body of ``work`` in ``source``."""
    module = ast.parse(source)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "work"
    )
    return set(resource_bound_names(function.body, module))


def test_the_list_is_the_one_the_decision_names() -> None:
    """An addition is a decision (docs/design/DECISIONS.md), so the list is pinned whole."""
    assert RESOURCE_CONSTRUCTORS == {
        "tempfile.TemporaryDirectory",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryFile",
        "tempfile.SpooledTemporaryFile",
        "multiprocessing.Pool",
        "multiprocessing.pool.Pool",
        "multiprocessing.pool.ThreadPool",
        "tkinter.PhotoImage",
        "tkinter.BitmapImage",
        "mmap.mmap",
        "weakref.finalize",
    }


def test_the_weak_reference_constructors_are_the_ones_the_decision_names() -> None:
    assert WEAK_REFERENCE_CONSTRUCTORS == {
        "weakref.ref",
        "weakref.proxy",
        "weakref.WeakMethod",
        "weakref.finalize",
    }


@pytest.mark.parametrize(
    "imports,statement,kept",
    [
        ("import weakref", "watcher = weakref.ref(target)", {"target"}),
        ("import weakref", "watcher = weakref.ref(target, callback)", {"target"}),
        ("from weakref import ref", "watcher = ref(target)", {"target"}),
        ("import weakref as w", "registry.append(w.proxy(target))", {"target"}),
        ("import weakref", "watcher = weakref.WeakMethod(target.method)", {"target"}),
        # The finalizer is listed as a resource too, though its registry keeps it alive.
        ("import weakref", "done = weakref.finalize(target, callback)", {"target", "done"}),
        # Only the object watched: a callback is held strongly by the reference.
        ("import weakref", "watcher = weakref.ref(make(), callback)", {"make"}),
        ("import weakref", "watcher = other.ref(target)", set()),
        ("import weakref", "watcher = weakref.getweakrefcount(target)", set()),
    ],
)
def test_a_local_watched_by_a_weak_reference_is_kept(
    imports: str, statement: str, kept: set[str]
) -> None:
    """Hostile fixture r131: a weak reference must not see its referent die at the helper's return."""
    source = f"{imports}\n\ndef work(target, callback, registry, make, other):\n    {statement}\n"
    assert _names(source) == kept


@pytest.mark.parametrize(
    "imports,call",
    [
        ("import tempfile", "tempfile.TemporaryDirectory()"),
        ("import tempfile as tf", "tf.NamedTemporaryFile('w')"),
        ("from tempfile import TemporaryDirectory", "TemporaryDirectory(prefix='x')"),
        ("from tempfile import TemporaryFile as Scratch", "Scratch()"),
        ("from tempfile import *", "SpooledTemporaryFile()"),
        ("import tempfile\nmake = tempfile.TemporaryDirectory", "make()"),
        ("import multiprocessing", "multiprocessing.Pool(2)"),
        ("import multiprocessing as mp", "mp.Pool(2)"),
        ("import multiprocessing.pool", "multiprocessing.pool.ThreadPool(2)"),
        ("from multiprocessing.pool import ThreadPool", "ThreadPool(2)"),
        ("from multiprocessing import Pool", "Pool()"),
        ("import tkinter as tk", "tk.PhotoImage(file=path)"),
        ("from tkinter import *", "BitmapImage(file=path)"),
        ("import mmap", "mmap.mmap(fd, 0)"),
        ("from mmap import mmap", "mmap(fd, 0)"),
    ],
)
def test_a_direct_call_resolves_through_the_modules_imports(imports: str, call: str) -> None:
    assert _names(f"{imports}\n\ndef work(path, fd, owner, callback):\n    held = {call}\n") == {
        "held"
    }


def test_an_import_inside_the_function_resolves_too() -> None:
    source = (
        "def work():\n    import tempfile as scratch\n    held = scratch.TemporaryDirectory()\n"
    )
    assert _names(source) == {"held"}


@pytest.mark.parametrize(
    "statement",
    [
        "held: object = tempfile.TemporaryDirectory()",
        "first = second = tempfile.TemporaryDirectory()",
        "print((held := tempfile.TemporaryDirectory()).name)",
        "if flag:\n        held = tempfile.TemporaryDirectory()",
    ],
)
def test_every_plain_name_binding_form_counts(statement: str) -> None:
    found = _names(f"import tempfile\n\ndef work(flag):\n    {statement}\n")
    assert found in ({"held"}, {"first", "second"})


@pytest.mark.parametrize(
    "imports,statement",
    [
        # An ordinary value, and constructors outside the criterion.
        ("import tempfile", "held = tempfile.gettempdir()"),
        ("import tempfile", "held = tempfile.mkdtemp()"),
        ("import threading", "held = threading.Lock()"),
        ("import socket", "held = socket.socket()"),
        ("", "held = open(path)"),
        # Not a direct call of the constructor.
        ("import tempfile", "held = tempfile.TemporaryDirectory"),
        ("import tempfile", "held = tempfile.TemporaryDirectory().name"),
        ("import tempfile", "held = wrap(tempfile.TemporaryDirectory())"),
        # Reachable through its container, or released by its statement.
        ("import tempfile", "self.held = tempfile.TemporaryDirectory()"),
        ("import tempfile", "cache['held'] = tempfile.TemporaryDirectory()"),
        ("import tempfile", "with tempfile.TemporaryDirectory() as held:\n        pass"),
        # Another module's name spelled the same.
        ("from project.scratch import TemporaryDirectory", "held = TemporaryDirectory()"),
        ("from .compat import TemporaryDirectory", "held = TemporaryDirectory()"),
        # A nested scope's binding is that scope's.
        ("import tempfile", "def inner():\n        held = tempfile.TemporaryDirectory()"),
    ],
)
def test_the_guard_is_silent_for_everything_else(imports: str, statement: str) -> None:
    assert _names(f"{imports}\n\ndef work(self, cache, path, wrap):\n    {statement}\n") == set()


# -- The guard in a run --------------------------------------------------------

REPORTS = """\
import os
import tempfile


def write_report(rows):
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "report.txt")
    with open(path, "w") as handle:
        for row in rows:
            handle.write(f"{row}\\n")
    size = os.path.getsize(path)
    print("report", sorted(os.listdir(os.path.dirname(path))), size)
    return size


def write_summary(rows):
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "summary.txt")
    with open(path, "w") as handle:
        for row in rows:
            handle.write(f"{row}\\n")
    size = os.path.getsize(path)
    print("summary", sorted(os.listdir(os.path.dirname(path))), size * 2)
    return size * 2


def plain_report(rows):
    lines = [str(row) for row in rows]
    text = "\\n".join(lines)
    count = len(text.split())
    width = max(len(line) for line in lines)
    print("report", count, width)
    return count


def plain_summary(rows):
    lines = [str(row) for row in rows]
    text = "\\n".join(lines)
    count = len(text.split())
    width = max(len(line) for line in lines)
    print("summary", count * 2, width)
    return count * 2


if __name__ == "__main__":
    print(write_report([1, 2, 3]), write_summary([4, 5]))
    print(plain_report([1, 22]), plain_summary([3, 44]))
"""


def _run(script: Path) -> Tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120, check=False
    )
    return result.returncode, result.stdout


def _refactored(tmp_path: Path, source: str, *, strict: bool = False) -> Tuple[str, Path, Path]:
    original = tmp_path / "original.py"
    original.write_text(source)
    changed = tmp_path / "changed.py"
    changed.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_to_fixed_point(str(changed))
    return changed.read_text(), original, changed


def test_a_temporary_directory_outlives_the_helper_that_made_it(tmp_path: Path) -> None:
    """The caller binds ``tmp`` under its own name, so the directory is there when it is listed."""
    generated, original, changed = _refactored(tmp_path, REPORTS)
    assert "tmp, path, size = __extracted_func_0('report.txt', rows)" in generated
    assert "tmp, path, size = __extracted_func_0('summary.txt', rows)" in generated
    assert "return (tmp, path, size)" in generated
    assert "_towel_" not in generated
    assert _run(changed) == _run(original)
    assert _run(original)[0] == 0


def test_without_the_guard_the_directory_would_be_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: with the list emptied, the same run deletes the directory under its caller."""
    monkeypatch.setattr("towel.unification.resource_guard.RESOURCE_CONSTRUCTORS", frozenset[str]())
    generated, original, changed = _refactored(tmp_path, REPORTS)
    assert "path, size = __extracted_func_0('report.txt', rows)" in generated
    assert "tmp" not in generated.split("def write_report")[1].split("def write_summary")[0]
    status, output = _run(changed)
    assert status != 0 and (status, output) != _run(original)


def test_the_guard_adds_nothing_to_an_ordinary_block(tmp_path: Path) -> None:
    """Silent for an ordinary value: only the names later code reads are returned."""
    generated, original, changed = _refactored(tmp_path, REPORTS)
    assert "count, width = __extracted_func_1(rows)" in generated
    assert "return (count, width)" in generated


WATCHED = """\
import weakref


class Target:
    pass


def first():
    target = Target()
    watcher = weakref.ref(target)
    label = "first"
    print(label, watcher() is not None)
    return watcher() is not None


def second():
    target = Target()
    watcher = weakref.ref(target)
    label = "second"
    if label:
        print(label, watcher() is not None)
    return watcher() is not None


if __name__ == "__main__":
    print(first(), second())
"""


def test_a_weak_reference_still_finds_its_referent_after_the_block(tmp_path: Path) -> None:
    """The caller keeps ``target``, which nothing reads again, so ``watcher()`` answers as before."""
    generated, original, changed = _refactored(tmp_path, WATCHED)
    assert "target, watcher, label = __extracted_func_0('first')" in generated
    assert _run(changed) == _run(original)
    assert _run(original) == (0, "first True\nsecond True\nTrue True\n")


def test_without_the_guard_the_reference_would_be_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: with the constructors forgotten, the same run returns a different value."""
    monkeypatch.setattr(
        "towel.unification.resource_guard.WEAK_REFERENCE_CONSTRUCTORS", frozenset[str]()
    )
    generated, original, changed = _refactored(tmp_path, WATCHED)
    assert "watcher, label = __extracted_func_0('first')" in generated
    assert _run(changed) == (0, "first False\nsecond False\nFalse False\n")


WHOLE_BODY = """\
import os
import tempfile


def first(rows):
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "first.txt")
    with open(path, "w") as handle:
        handle.write(str(rows))
    return os.path.getsize(path)


def second(rows):
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "second.txt")
    with open(path, "w") as handle:
        handle.write(str(rows))
    return os.path.getsize(path)


if __name__ == "__main__":
    print(first([1, 2]), second([3]))
"""


def test_a_block_that_ends_its_function_returns_no_resource(tmp_path: Path) -> None:
    """Nothing runs between the helper's return and the function's: the directory lives as long."""
    generated, original, changed = _refactored(tmp_path, WHOLE_BODY)
    assert "    return __extracted_func_0('first.txt', rows)\n" in generated
    assert "tmp" not in generated.split("def first")[1]
    assert _run(changed) == _run(original)


INSIDE_A_HANDLER = """\
import os
import tempfile


def first(rows):
    try:
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "first.txt")
        count = len(rows) // len(rows)
        print("first", count)
    except ZeroDivisionError:
        print("first: empty", os.path.isdir(os.path.dirname(path)))
    print("first done")


def second(rows):
    try:
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "second.txt")
        count = len(rows) // len(rows)
        print("second", count)
    except ZeroDivisionError:
        print("second: empty", os.path.isdir(os.path.dirname(path)))
    finally:
        print("second finally")
    print("second done")


if __name__ == "__main__":
    first([1])
    first([])
    second([2])
    second([])
"""


def test_a_resource_bound_inside_a_handler_that_stays_is_left_alone(tmp_path: Path) -> None:
    """A helper that raises returns nothing, so the handler would find the directory gone."""
    generated, original, changed = _refactored(tmp_path, INSIDE_A_HANDLER)
    assert generated == INSIDE_A_HANDLER


def test_strict_mode_never_consults_the_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode holds every opaque local, a listed resource or not, exactly as 1.949 did."""
    strict, _, _ = _refactored(tmp_path, REPORTS, strict=True)
    monkeypatch.setattr("towel.unification.resource_guard.RESOURCE_CONSTRUCTORS", frozenset[str]())
    emptied, _, _ = _refactored(tmp_path, REPORTS, strict=True)
    assert strict == emptied
