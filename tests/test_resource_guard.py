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
    resource_guard,
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
        "builtins.open",
        "io.open",
        "io.FileIO",
        "os.fdopen",
        "socket.socket",
        "socket.socketpair",
        "socket.create_connection",
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
        "weakref.WeakValueDictionary",
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
        ("import socket", "socket.socket()"),
        ("", "open(path)"),
        ("import io", "io.open(path)"),
        ("import os", "os.fdopen(fd)"),
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
        # Not a direct call of the constructor.
        ("import tempfile", "held = tempfile.TemporaryDirectory"),
        # Released by its complete with statement.
        ("import tempfile", "with tempfile.TemporaryDirectory() as held:\n        pass"),
        # Another module's name spelled the same.
        ("from project.scratch import TemporaryDirectory", "held = TemporaryDirectory()"),
        ("from .compat import TemporaryDirectory", "held = TemporaryDirectory()"),
        # A nested scope's binding is that scope's.
        ("import tempfile", "def inner():\n        held = tempfile.TemporaryDirectory()"),
    ],
)
def test_the_guard_is_silent_for_everything_else(imports: str, statement: str) -> None:
    result = _guard(f"{imports}\n\ndef work(self, cache, path, wrap):\n    {statement}\n")
    assert result.names == frozenset()
    assert result.refusal is None


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
    assert 'tmp, path, size = __extracted_func_0(rows, "report.txt")' in generated
    assert 'tmp, path, size = __extracted_func_0(rows, "summary.txt")' in generated
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
    assert 'path, size = __extracted_func_0(rows, "report.txt")' in generated
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
    assert 'target, watcher, label = __extracted_func_0("first")' in generated
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
    assert 'watcher, label = __extracted_func_0("first")' in generated
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
    assert '    return __extracted_func_0(rows, "first.txt")\n' in generated
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


def _guard(source: str, *, prefix: int | None = None):
    module = ast.parse(source)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "work"
    )
    return resource_guard(function.body[:prefix], module)


@pytest.mark.parametrize(
    "statement",
    [
        "held = tempfile.TemporaryDirectory(); backup = held; held = None",
        "held = tempfile.TemporaryDirectory(); backup = held; del held",
        "held = tempfile.TemporaryDirectory(); held += extra",
        "held = tempfile.TemporaryDirectory();\n    if flag:\n        held = None",
        "held = tempfile.TemporaryDirectory();\n    for held in values:\n        pass",
        "held = tempfile.TemporaryDirectory();\n    try:\n        fail()\n    except Exception as held:\n        pass",
        'held = tempfile.TemporaryDirectory();\n    match extra:\n        case {"x": held}:\n            pass',
        "box.held = tempfile.TemporaryDirectory()",
        "box[0] = tempfile.TemporaryDirectory()",
        "held = [tempfile.TemporaryDirectory()]",
        "held, = (tempfile.TemporaryDirectory(),)",
        "held = tempfile.TemporaryDirectory() if flag else None",
        "consume(tempfile.TemporaryDirectory())",
        "held = tempfile.TemporaryDirectory().name",
        "def later(held=tempfile.TemporaryDirectory()):\n        pass",
    ],
)
def test_unretained_constructor_is_declined(statement: str) -> None:
    """Review defect 1 and ownership gaps: recognize every listed call, not only assignments."""
    result = _guard(f"import tempfile\ndef work(flag, values, extra, box):\n    {statement}\n")
    assert result.refusal is not None
    assert "line " in result.refusal and "--strict" in result.refusal


@pytest.mark.parametrize(
    "weak",
    ["ref(target)", "proxy(target)", "WeakMethod(target.method)", "finalize(target, callback)"],
)
def test_weak_reference_rebinding_is_declined_even_outside_prefix(weak: str) -> None:
    """Review defect 1: a shorter candidate cannot hide the later rebinding."""
    source = f"import weakref\ndef work():\n    target = Target()\n    backup = target\n    watched = weakref.{weak}\n    target = None\n"
    assert _guard(source, prefix=3).refusal is not None


def test_constructor_rebinding_is_declined_even_outside_prefix() -> None:
    """Review defect 1: retaining a name only until a later reassignment loses its alias."""
    source = "import tempfile\ndef work():\n    held = tempfile.TemporaryDirectory()\n    backup = held\n    path = held.name\n    held = None\n"
    assert _guard(source, prefix=3).refusal is not None


@pytest.mark.parametrize(
    "source",
    [
        "import tempfile\ndef work(tempfile):\n    held = tempfile.TemporaryDirectory()\n",
        "import tempfile\ndef work(fake):\n    tempfile = fake\n    held = tempfile.TemporaryDirectory()\n",
        "from tempfile import TemporaryDirectory as TD\ndef work(TD):\n    held = TD()\n",
        "import tempfile\ndef unrelated():\n    import tempfile as tf\ndef work(tf):\n    held = tf.TemporaryDirectory()\n",
        'def work(open):\n    held = open("x")\n',
        "import tempfile\ndef work():\n    try:\n        fail()\n    except Exception as tempfile:\n        held = tempfile.TemporaryDirectory()\n",
        "import tempfile\ndef work(values):\n    held = [tempfile.TemporaryDirectory() for tempfile in values]\n",
    ],
)
def test_shadowing_is_resolved_in_the_actual_scope(source: str) -> None:
    """Review scope note: same spelling does not make a local/parameter an imported constructor."""
    result = _guard(source)
    assert result.names == frozenset()
    assert result.refusal is None


def test_a_comprehension_target_does_not_shadow_the_enclosing_import() -> None:
    """Scope regression: comprehension bindings cannot hide an outer listed constructor."""
    source = "import tempfile\ndef work(values):\n    items = [tempfile for tempfile in values]\n    held = tempfile.TemporaryDirectory()\n"
    assert _guard(source).names == {"held"}


@pytest.mark.parametrize(
    "call",
    [
        'open("x")',
        'io.open("x")',
        "os.fdopen(fd)",
        "socket.socket()",
        "tempfile.TemporaryDirectory()",
    ],
)
def test_whole_with_statement_manages_the_resource(call: str) -> None:
    """Coordinator rule 1: a whole with owns cleanup without returning its target."""
    result = _guard(
        f"import io, os, socket, tempfile\ndef work(fd):\n    with {call} as held:\n        use(held)\n    held = None\n"
    )
    assert result.names == frozenset()
    assert result.refusal is None


def test_a_later_module_assignment_cannot_hide_an_earlier_resource_call(tmp_path: Path) -> None:
    """Scope regression: functions can run before the module replaces their imported global."""
    source = REPORTS + "\ntempfile = None\n"
    _, original, changed = _refactored(tmp_path, source)
    assert _run(original)[0] == 0
    assert _run(changed) == _run(original)


def test_deleting_a_module_shadow_restores_builtin_open(tmp_path: Path) -> None:
    """Scope regression: a deleted module name must not hide the owning builtin file."""
    from tests.test_relaxed_review import CASES

    source = "open = None\ndel open\n" + CASES["ext_descriptor_open"]["source"]
    _, original, changed = _refactored(tmp_path, source)
    assert _run(original)[0] == 0
    assert _run(changed) == _run(original)


def test_keyword_weakmethod_keeps_its_receiver() -> None:
    """The keyword form has the same weak ownership as its positional counterpart."""
    result = _guard(
        "import weakref\ndef work():\n    target = Target()\n    watched = weakref.WeakMethod(meth=target.method)\n"
    )
    assert result.names == {"target"}
    assert result.refusal is None


def test_a_weak_reference_to_an_expression_is_declined() -> None:
    """An arbitrary expression does not establish a stable local owner."""
    result = _guard("import weakref\ndef work():\n    watched = weakref.ref(make())\n")
    assert result.refusal is not None


@pytest.mark.parametrize(
    "conditional",
    [
        "try:\n        pass\n    except* ValueError:\n        make = None",
        "False and (make := None)",
        "(make := None) if False else None",
        "assert True, (make := None)",
    ],
)
def test_conditional_shadowing_does_not_erase_the_import(conditional: str) -> None:
    """A skipped binding leaves the constructor available on another path."""
    source = f"from tempfile import TemporaryDirectory\ndef work():\n    make = TemporaryDirectory\n    {conditional}\n    held = make()\n"
    assert _guard(source).names == {"held"}


def test_a_closure_can_rebind_a_resource_owner() -> None:
    """A nonlocal write is another path that prevents proof of stable ownership."""
    source = "import tempfile\ndef work():\n    held = tempfile.TemporaryDirectory()\n    backup = held\n    def rebind():\n        nonlocal held\n        held = None\n    rebind()\n"
    assert _guard(source).refusal is not None


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
def test_import_writes_in_the_declared_scope_are_visible(declaration: str) -> None:
    """A global/nonlocal declaration does not erase an import executed before the call."""
    body = f"def work():\n    {declaration} tempfile\n    import tempfile\n    held = tempfile.TemporaryDirectory()\n"
    if declaration == "nonlocal":
        from textwrap import indent

        body = "def outer():\n    tempfile = None\n" + indent(body, "    ")
    module = ast.parse(body)
    function = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "work"
    )
    assert resource_guard(function.body, module).names == {"held"}


@pytest.mark.parametrize("constructor", ["tempfile.TemporaryDirectory()", "weakref.ref(target)"])
def test_generated_helpers_cannot_hide_their_returned_owner(constructor: str) -> None:
    """Fixed-point regression: a later helper must not discard earlier retained ownership."""
    source = f"import tempfile, weakref\ndef __extracted_func_0(target):\n    held = {constructor}\n    return held\ndef __extracted_func_1(target):\n    return __extracted_func_0(target)\ndef work(target):\n    held = __extracted_func_1(target)\n"
    assert _guard(source).refusal is not None


def test_a_generated_helper_with_managed_resources_can_still_move() -> None:
    """An entire internal with statement does not transfer resource ownership to its caller."""
    source = "def __extracted_func_0(path):\n    with open(path) as handle:\n        return handle.read()\ndef work(path):\n    text = __extracted_func_0(path)\n"
    assert _guard(source).refusal is None


def test_an_import_alias_cannot_hide_a_generated_helpers_ownership() -> None:
    """A cross-module generated helper needs the same protection through an alias."""
    source = (
        "from helpers import __extracted_func_0 as make\ndef work():\n    held, path = make()\n"
    )
    assert _guard(source).refusal is not None


def test_a_local_helper_cannot_supply_an_imported_helpers_ownership() -> None:
    """Summary identities retain qualification: a same-spelled local body proves nothing abroad."""
    source = "from provider import __extracted_func_0 as make\ndef __extracted_func_0():\n    return 1\ndef work():\n    held, path = make()\n"
    assert _guard(source).refusal is not None


@pytest.mark.parametrize("constructor", ["weakref.ref(target)", "tempfile.TemporaryDirectory()"])
@pytest.mark.parametrize("loop", ["for i in values:", "while flag:", "async for i in values:"])
@pytest.mark.parametrize("whole_loop", [False, True])
def test_loop_back_edges_prevent_stable_ownership(
    constructor: str, loop: str, whole_loop: bool
) -> None:
    """Round 2 defect 1: a binding before the call can overwrite it next iteration."""
    module = ast.parse(
        "import tempfile, weakref\nasync def work(values, flag):\n    "
        + loop
        + "\n        target = make()\n        held = "
        + constructor
        + "\n"
    )
    function = module.body[1]
    assert isinstance(function, ast.AsyncFunctionDef)
    outer = function.body[0]
    assert isinstance(outer, (ast.For, ast.AsyncFor, ast.While))
    block = function.body if whole_loop else outer.body[1:]
    assert resource_guard(block, module).refusal is not None


@pytest.mark.parametrize(
    "expression",
    [
        "[(held := tempfile.TemporaryDirectory()) for i in values]",
        "{i: (held := tempfile.TemporaryDirectory()) for i in values}",
        "{(held := tempfile.TemporaryDirectory()) for i in values}",
        "((held := tempfile.TemporaryDirectory()) for i in values)",
        "[weakref.ref(target := make()) for i in values]",
        "[weakref.ref(target) for target in values]",
    ],
)
def test_comprehensions_are_loops_for_resource_ownership(expression: str) -> None:
    """Round 2 suspicion: a comprehension's elt is repeated, including walrus stores."""
    assert _guard("import tempfile, weakref\ndef work(values):\n    held = " + expression).refusal


@pytest.mark.parametrize(
    "constructor, descriptor",
    [
        ("io.FileIO(os.devnull, 'rb')", "held.fileno()"),
        ("socket.socketpair()", "held[0].fileno()"),
        ("socket.create_connection(server.getsockname())", "held.fileno()"),
    ],
)
def test_additional_descriptor_owners_survive_helper_return(
    tmp_path: Path, constructor: str, descriptor: str
) -> None:
    """Round 2 suspicion: all three constructors release an independently used descriptor."""
    source = "import io, os, socket\n"
    if "create_connection" in constructor:
        source += "server = socket.socket()\nserver.bind(('127.0.0.1', 0))\nserver.listen()\n"
    for name in ("first", "second"):
        source += (
            f"def {name}(n):\n    held = {constructor}\n    descriptor = {descriptor}\n"
            "    total = n + 1\n    total *= 2\n"
            + ("    if n:\n        " if name == "second" else "    ")
            + f"print('{name}', os.fstat(descriptor).st_mode, total)\n    return total\n"
        )
    source += "first(1)\nsecond(2)\n"
    generated, original, changed = _refactored(tmp_path, source)
    assert _run(original)[0] == 0
    assert _run(changed) == _run(original), generated


def test_weak_value_dictionary_keeps_its_initial_values(tmp_path: Path) -> None:
    """Round 2 suspicion: a weak value dictionary observes early release just like ref."""
    source = "import weakref\nclass Thing: pass\n"
    for name in ("first", "second"):
        source += (
            f"def {name}(n):\n    targets = {{'item': Thing()}}\n"
            "    watcher = weakref.WeakValueDictionary(targets)\n"
            "    total = n + 1\n    total *= 2\n"
            + ("    if n:\n        " if name == "second" else "    ")
            + f"print('{name}', len(watcher), total)\n    return total\n"
        )
    source += "first(1)\nsecond(2)\n"
    generated, original, changed = _refactored(tmp_path, source)
    assert _run(original)[0] == 0
    assert _run(changed) == _run(original), generated
