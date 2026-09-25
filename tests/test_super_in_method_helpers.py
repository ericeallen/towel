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

"""Zero-argument ``super()`` moves only into a method helper of the class that holds it.

``super()`` is ``super(__class__, first)``: the class cell the compiler gives
every function of a class body that loads ``super`` or ``__class__``, and the
calling frame's first argument. A class-private helper compiled in the class
that holds both duplicates, reached as ``self.__extracted_func_0()``, has the
same cell and the same receiver first, so the code means there what it meant
in the method (docs/DECISIONS.md, "A method helper lives in the class that
holds both duplicates"). Every other home is declined rather than taken: a
module function has no cell, and a class that cannot keep a plain member, or
a method whose receiver the helper would not share, gets nothing.

Every test runs the program before and after in a fresh interpreter and
compares what it printed.
"""

from __future__ import annotations

import ast
import contextlib
from dataclasses import dataclass
import io
import logging
from pathlib import Path
import textwrap
from typing import List

import pytest

from tests.test_class_private_helpers import (
    _class_helpers,
    _module_helpers,
    _project,
    _run,
    _strict_errors,
    requires_checkers,
)
from towel.unification.class_private import is_class_private
from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine

DRIVER = "from pkg.lib import main\nmain()\n"


@dataclass(frozen=True)
class _Outcome:
    """What a fixed-point run did to ``pkg/lib.py``, and why it declined the pairs it did."""

    applied: int
    source: str
    reasons: List[str]


def _refactored(tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture) -> _Outcome:
    """Refactor ``pkg/lib.py`` holding ``source`` to a fixed point, tracing why pairs were declined.

    The program must print the same before and after, and nothing on stderr.
    """
    root = _project(tmp_path, {"pkg/__init__.py": "", "pkg/lib.py": source})
    before = _run(root, DRIVER, root)
    engine = UnificationRefactorEngine(min_lines=3)
    with (
        caplog.at_level(logging.DEBUG, logger="towel.rejections"),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none"
        )
    after = _run(root, DRIVER, root)
    assert before == after, (before, after)
    assert before.endswith("|"), before  # nothing on stderr
    return _Outcome(
        applied=sum(applied for applied, _ in results.values()),
        source=(root / "pkg" / "lib.py").read_text(),
        reasons=[
            record.getMessage()[len("REJECT[") :].split("]")[0]
            for record in caplog.records
            if record.name == "towel.rejections" and record.getMessage().startswith("REJECT[")
        ],
    )


def _helpers_in(source: str, host: str) -> List[ast.FunctionDef]:
    """Each generated helper defined in the body of class ``host``."""
    return [
        item
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and node.name == host
        for item in node.body
        if isinstance(item, ast.FunctionDef) and is_generated_helper_name(item.name)
    ]


# -- the helper is a method of the class holding both duplicates ----------------------

SINGLE = """
class Base:
    def greet(self, name):
        return f"base:{name}"


class Child(Base):
    def __init__(self):
        self.count = 0

    def first(self, name):
        print("first", name)
        self.count += 1
        prefix = super().greet(name)
        result = prefix + "/" + __class__.__name__ + str(self.count)
        return result.upper()

    def second(self, name):
        print("second", name)
        self.count += 1
        prefix = super().greet(name)
        result = prefix + "/" + __class__.__name__ + str(self.count)
        return result.lower()


class Grandchild(Child):
    def greet(self, name):
        return "grand:" + name

    def first(self, name):
        return "g" + super().first(name)


def main():
    for obj in (Child(), Grandchild()):
        print(obj.first("ab"), obj.second("cd"), obj.greet("x"), obj.count)
"""

DIAMOND = """
class A:
    def who(self, name):
        return "A:" + name


class B(A):
    def who(self, name):
        return "B>" + super().who(name)


class C(A):
    def who(self, name):
        return "C>" + super().who(name)


class D(B, C):
    tag = "d"

    def first(self, name):
        print("first", name)
        prefix = super().who(name)
        suffix = __class__.__name__ + self.tag
        result = prefix + "/" + suffix + str(len(name))
        return result.upper()

    def second(self, name):
        print("second", name)
        prefix = super().who(name)
        suffix = __class__.__name__ + self.tag
        result = prefix + "/" + suffix + str(len(name))
        return result.lower()


class E(D):
    tag = "e"

    def who(self, name):
        return "E!" + name


def main():
    print([cls.__name__ for cls in E.__mro__])
    for obj in (D(), E()):
        print(obj.first("ab"), obj.second("cd"))
"""

COOPERATIVE_INIT = """
class Root:
    def __init__(self, **kwargs):
        self.trail = ["Root"]
        self.extra = dict(kwargs)


class Left(Root):
    def __init__(self, left=1, **kwargs):
        super().__init__(**kwargs)
        self.trail.append("Left")
        self.left = left


class Right(Root):
    def __init__(self, right=2, **kwargs):
        super().__init__(**kwargs)
        self.trail.append("Right")
        self.right = right


class Both(Left, Right):
    def __init__(self, **kwargs):
        print("init", sorted(kwargs))
        super().__init__(**kwargs)
        self.trail.append("Both")
        self.size = len(self.trail)

    def reset(self, **kwargs):
        print("reset", sorted(kwargs))
        super().__init__(**kwargs)
        self.trail.append("Both")
        self.size = len(self.trail)
        return self.size


class More(Both):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.trail.append("More")


def main():
    for kind in (Both, More):
        obj = kind(left=5, right=6)
        print(obj.trail, obj.left, obj.right, obj.size, obj.extra)
        print(obj.reset(left=7), obj.trail, obj.left, obj.right)
"""

CLASSMETHODS = """
class Shape:
    @classmethod
    def build(cls, n):
        return [cls.__name__] * n


class Square(Shape):
    sides = 4

    @classmethod
    def small(cls, n):
        print("small", n)
        parts = super().build(n)
        parts.append(cls.sides)
        return tuple(parts)

    @classmethod
    def large(cls, n):
        print("large", n)
        parts = super().build(n)
        parts.append(cls.sides)
        return list(parts)


class Tiny(Square):
    sides = 3

    @classmethod
    def build(cls, n):
        return ["tiny"]


def main():
    print(Square.small(2), Square.large(1), Tiny.small(2), Tiny().large(1))
"""

# ``super()`` inside a nested scope of the moved code: a lambda reads its own
# first argument (a ``Grand``, not the receiver), a lambda with none raises, and
# a comprehension reads the method's receiver where it is inlined (3.12 on) and
# raises where it is not.
NESTED_SCOPES = """
class Base:
    def who(self):
        return "base:" + type(self).__name__


class Child(Base):
    marker = "c"

    def first(self, items):
        print("first", len(items))
        tail = (lambda obj: super().who() + obj.marker)(Grand())
        try:
            names = [super().who() + self.marker for _ in items]
        except TypeError as error:
            names = [type(error).__name__]
        try:
            bare = (lambda: super().who())()
        except RuntimeError as error:
            bare = str(error)
        return tail, names, bare

    def second(self, items):
        print("second", len(items))
        tail = (lambda obj: super().who() + obj.marker)(Grand())
        try:
            names = [super().who() + self.marker for _ in items]
        except TypeError as error:
            names = [type(error).__name__]
        try:
            bare = (lambda: super().who())()
        except RuntimeError as error:
            bare = str(error)
        return tail, names, bare


class Grand(Child):
    marker = "g"


def main():
    print(Child().first([1, 2]), Child().second([3]))
"""


@pytest.mark.parametrize(
    "source, host",
    [
        (SINGLE, "Child"),
        (DIAMOND, "D"),
        (COOPERATIVE_INIT, "Both"),
        (CLASSMETHODS, "Square"),
        (NESTED_SCOPES, "Child"),
    ],
    ids=["single-inheritance", "diamond", "cooperative-init", "classmethod", "nested-scopes"],
)
def test_super_moves_into_a_helper_of_the_class_holding_both_duplicates(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, source: str, host: str
) -> None:
    """The helper holds the ``super()`` calls, lives in ``host``, and the program prints the same.

    A subclass that overrides the method ``super()`` calls (``Grandchild``,
    ``E``, ``Tiny``) still reaches the next class after ``host``, and a
    diamond still resolves ``B``, ``C``, ``A`` in order; a helper in any other
    class would have started the search elsewhere.
    """
    outcome = _refactored(tmp_path, source, caplog)
    assert outcome.applied == 1
    text = outcome.source
    helpers = _class_helpers(text)
    assert len(helpers[host]) == 1 and is_class_private(helpers[host][0]), helpers
    assert not _module_helpers(text)
    assert all(not names for cls, names in helpers.items() if cls != host), helpers
    (helper,) = _helpers_in(text, host)
    assert "super()." in ast.unparse(helper), text
    # The helper reads its own cell; a parameter of the name would hide it.
    assert "__class__" not in [argument.arg for argument in helper.args.args]


def test_a_helper_reads_the_class_cell_bare_and_the_call_passes_nothing_for_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``__class__`` beside ``super()`` is the helper's own cell, not an argument of the call."""
    text = _refactored(tmp_path, DIAMOND, caplog).source
    tree = ast.parse(text)
    (helper,) = _helpers_in(text, "D")
    assert "__class__" not in [argument.arg for argument in helper.args.args]
    assert "__class__.__name__" in ast.unparse(helper)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == helper.name
    ]
    assert len(calls) == 2
    assert all("__class__" not in ast.unparse(call) for call in calls)


CLUSTER = """
class Base:
    def who(self):
        return "base:" + type(self).__name__


class One(Base):
    tag = "1"

    def first(self, n):
        print("step", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.upper(), size

    def second(self, n):
        print("step", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.lower(), size

    def third(self, n):
        print("step", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.title(), size

    def fourth(self, n, other=None):
        if other is not None:
            self = other
        print("step", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.swapcase(), size


class Two(One):
    tag = "2"

    def fifth(self, n):
        print("step", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.casefold(), size


def main():
    for obj in (One(), Two()):
        print(obj.first(1), obj.second(2), obj.third(3), obj.fourth(4), obj.fourth(5, Two()))
    print(Two().fifth(6))
"""


def test_only_methods_of_the_class_with_the_same_receiver_share_the_helper(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Three sites call ``One``'s helper; ``fourth`` rebinds ``self`` and ``Two.fifth`` is another class.

    ``super()`` in ``fourth`` reads whatever ``self`` holds when it runs, and in
    ``Two`` it starts after ``Two``; each keeps its own code.
    """
    text = _refactored(tmp_path, CLUSTER, caplog).source
    helpers = _class_helpers(text)
    assert len(helpers["One"]) == 1 and not helpers["Two"] and not _module_helpers(text)
    methods = {
        item.name: ast.unparse(item)
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ClassDef)
        for item in node.body
        if isinstance(item, ast.FunctionDef)
    }
    helper = helpers["One"][0]
    assert all(f"self.{helper}(" in methods[name] for name in ("first", "second", "third"))
    assert "super().who()" in methods["fourth"] and "super().who()" in methods["fifth"]


# -- every other home is declined, not taken -------------------------------------------

SHARED_METHODS = """
    def first(self, n):
        print("first", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.upper(), size

    def second(self, n):
        print("second", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.lower(), size
"""

BASE = """
class Base:
    def who(self):
        return "base"
"""


def _in_class(header: str, prelude: str = BASE, driver: str = "One") -> str:
    """``prelude``, then a class ``header`` holding the shared methods, and a ``main`` running them."""
    return (
        prelude
        + f"\n\n{header}\n    tag = '1'\n"
        + textwrap.indent(textwrap.dedent(SHARED_METHODS), "    ")
        + f"\n\ndef main():\n    print({driver}().first(1), {driver}().second(2))\n"
    )


DECORATED = _in_class(
    "@register\nclass One(Base):",
    prelude=BASE + "\n\nREGISTRY = []\n\n\ndef register(cls):\n    REGISTRY.append(cls)\n"
    "    return cls\n",
)

METACLASS = _in_class(
    "class One(Base, metaclass=Meta):",
    prelude=BASE + """

class Meta(type):
    def __new__(mcs, name, bases, namespace):
        namespace["members"] = sorted(key for key in namespace if not key.startswith("__"))
        return super().__new__(mcs, name, bases, namespace)
""",
)

INIT_SUBCLASS = _in_class(
    "class One(Base):",
    prelude="""
class Base:
    seen = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        Base.seen.append(sorted(key for key in vars(cls) if not key.startswith("__")))

    def who(self):
        return "base"
""",
)

GETATTRIBUTE = _in_class(
    "class One(Base):",
    prelude=BASE + "\n    def __getattribute__(self, name):\n"
    "        return object.__getattribute__(self, name)\n",
)

UNDERSCORES = _in_class("class __(Base):", driver="__")

PROTOCOL = """
from typing import Protocol


class Tagged(Protocol):
    tag: str

    def first(self, n):
        print("first", n)
        text = super().__getattribute__("tag") + self.tag
        size = len(text) + n
        return text.upper(), size

    def second(self, n):
        print("second", n)
        text = super().__getattribute__("tag") + self.tag
        size = len(text) + n
        return text.lower(), size


class Impl(Tagged):
    tag = "impl"


def main():
    print(Impl().first(1), Impl().second(2))
"""

SELF_TYPE = "from typing import Protocol\n\n\nclass HasTag(Protocol):\n    tag: str\n" + _in_class(
    "class One(Base):"
).replace("(self, n)", "(self: HasTag, n)")

SIBLINGS = BASE + """

class One(Base):
    tag = "1"

    def first(self, n):
        print("first", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.upper(), size


class Two(Base):
    tag = "2"

    def second(self, n):
        print("second", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.lower(), size


def main():
    print(One().first(1), Two().second(2))
"""

# A method that never reads an attribute of its receiver runs with ``None`` in
# its place, and ``super(One, None)`` is an unbound super whose own attributes
# still answer: a helper reached through ``self`` would raise instead. (From
# 3.12 a call site that has run before raises ``TypeError`` there; the
# receiver-less call comes first.)
RECEIVER_NEVER_READ = """
class Base:
    def __repr__(self):
        return "Base()"


class One(Base):
    def first(self, n):
        print("first", n)
        text = super().__repr__() + "!"
        size = len(text) + n
        return text.upper(), size

    def second(self, n):
        print("second", n)
        text = super().__repr__() + "!"
        size = len(text) + n
        return text.lower(), size


def main():
    print(One.first(None, 3))
    print(One().first(1), One().second(2))
"""

NESTED_FUNCTION = BASE + """

class One(Base):
    tag = "1"

    def outer(self, n):
        def first(obj, n):
            print("first", n)
            text = super().who() + obj.tag
            size = len(text) + n
            return text.upper(), size

        def second(obj, n):
            print("second", n)
            text = super().who() + obj.tag
            size = len(text) + n
            return text.lower(), size

        return first(self, n), second(self, n + 1)


def main():
    print(One().outer(1))
"""

RECEIVER_REBOUND = """
class Base:
    def who(self):
        return "base:" + type(self).__name__


class One(Base):
    tag = "1"

    def first(self, n, other=None):
        if other is not None:
            self = other
        print("first", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.upper(), size

    def second(self, n, other=None):
        if other is not None:
            self = other
        print("second", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.lower(), size


class Two(One):
    tag = "2"


def main():
    print(One().first(1, Two()), One().second(2))
"""

# A method binding ``__class__`` has no cell, so its ``super()`` raises; a
# helper would have one if the binding stayed behind.
CLASS_CELL_BOUND = BASE + """

class One(Base):
    tag = "1"

    def first(self, n):
        __class__ = type(self)
        print("first", n)
        try:
            text = super().who() + self.tag
        except RuntimeError as error:
            text = str(error)
        size = len(text) + n
        return text.upper(), size, __class__.__name__

    def second(self, n):
        __class__ = type(self)
        print("second", n)
        try:
            text = super().who() + self.tag
        except RuntimeError as error:
            text = str(error)
        size = len(text) + n
        return text.lower(), size, __class__.__name__


def main():
    print(One().first(1), One().second(2))
"""

# The blocks differ in ``self.who()`` against ``super().who()``: the second call
# would pass ``super()`` as a thunk, a lambda with no receiver.
SUPER_AS_ARGUMENT = BASE + """

class One(Base):
    tag = "1"

    def who(self):
        return "one"

    def first(self, n):
        print("first", n)
        text = self.who().strip() + self.tag
        size = len(text) + n
        return text.upper(), size

    def second(self, n):
        print("second", n)
        text = super().who().strip() + self.tag
        size = len(text) + n
        return text.lower(), size


def main():
    print(One().first(1), One().second(2))
"""

# ``S()`` reads the method's cell, which the method has only because of the
# ``super()`` in the shared block; with that moved out it would have none.
ALIAS_OUTSIDE = """
S = super


class Base:
    def who(self):
        return "base"

    def other(self):
        return "other"


class One(Base):
    tag = "1"

    def first(self, n):
        print("first", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.upper(), size, S().other()

    def second(self, n):
        print("second", n)
        text = super().who() + self.tag
        size = len(text) + n
        return text.lower(), size, S().other()


def main():
    print(One().first(1), One().second(2))
"""

# ``s = super; s()`` works in a method, which loads ``super``; moved into the
# module function sibling classes would share, it raised ``RuntimeError``.
ALIAS_IN_SIBLINGS = BASE + """

class One(Base):
    tag = "1"

    def first(self, n):
        print("first", n)
        s = super
        text = s().who() + self.tag
        size = len(text) + n
        return text.upper(), size


class Two(Base):
    tag = "2"

    def second(self, n):
        print("second", n)
        s = super
        text = s().who() + self.tag
        size = len(text) + n
        return text.lower(), size


def main():
    print(One().first(1), Two().second(2))
"""


@pytest.mark.parametrize(
    "source, reason",
    [
        # A class whose machinery fails the method-host test keeps every
        # method's code, super() or not (decorator_reach).
        (PROTOCOL, "class_machinery_may_transform_methods"),
        (DECORATED, "needs_class_body"),
        (METACLASS, "class_machinery_may_transform_methods"),
        (INIT_SUBCLASS, "class_machinery_may_transform_methods"),
        (GETATTRIBUTE, "class_machinery_may_transform_methods"),
        (SELF_TYPE, "needs_class_body"),
        (UNDERSCORES, "needs_class_body"),
        (SIBLINGS, "needs_class_body"),
        (RECEIVER_NEVER_READ, "needs_class_body"),
        (NESTED_FUNCTION, "needs_class_body"),
        (RECEIVER_REBOUND, "frame_sensitive_block"),
        (CLASS_CELL_BOUND, "frame_sensitive_block"),
        (SUPER_AS_ARGUMENT, "super_in_call"),
        (ALIAS_OUTSIDE, "frame_read_in_function"),
        (ALIAS_IN_SIBLINGS, "frame_sensitive_block"),
    ],
    ids=[
        "protocol",
        "class-decorator",
        "metaclass",
        "init-subclass",
        "getattribute",
        "declared-self-type",
        "underscore-class-name",
        "different-classes",
        "receiver-never-read",
        "nested-function",
        "receiver-rebound",
        "class-cell-bound",
        "super-passed-by-the-call",
        "aliased-super-outside-the-block",
        "aliased-super-in-sibling-classes",
    ],
)
def test_super_is_declined_where_no_helper_of_its_class_can_hold_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, source: str, reason: str
) -> None:
    """Nothing is extracted, for the stated reason, and no module function takes the code instead."""
    outcome = _refactored(tmp_path, source, caplog)
    assert outcome.applied == 0, outcome.source
    assert outcome.source == textwrap.dedent(source).lstrip("\n")
    assert any(key.partition("[")[0] == reason for key in outcome.reasons), outcome.reasons


EXPLICIT = BASE + """

class One(Base):
    tag = "1"

    def first(self, n):
        print("first", n)
        text = super(One, self).who() + self.tag
        size = len(text) + n
        return text.upper(), size


class Two(Base):
    tag = "2"

    def second(self, n):
        print("second", n)
        text = super(Two, self).who() + self.tag
        size = len(text) + n
        return text.lower(), size


def main():
    print(One().first(1), Two().second(2))
"""


def test_super_naming_its_class_and_object_still_moves_to_a_module_function(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``super(One, self)`` reads no cell: sibling classes share a module function for it."""
    text = _refactored(tmp_path, EXPLICIT, caplog).source
    assert len(_module_helpers(text)) == 1
    assert not any(_class_helpers(text).values())


@pytest.mark.parametrize(
    "statement, needed",
    [
        ("x = super().f()", True),
        ("x = super(C, self).f()", False),
        ("x = super(C).f", False),
        ("x = super(*args).f()", True),
        ("x = super(**kwargs).f()", True),
        ("s = super", True),
        ("register(super)", True),
        ("x = (lambda: super().f())()", True),
        ("x = [super().f() for _ in y]", True),
        ("x = builtins.super().f()", False),
        ("x = self.super()", False),
        ("x = __class__.__name__", False),
    ],
)
def test_which_code_needs_its_class_body(statement: str, needed: bool) -> None:
    from towel.unification.semantic_safety import needs_class_body

    assert needs_class_body(ast.parse(statement).body) is needed


def test_super_reached_through_an_alias_requires_the_original_frame() -> None:
    """``s()`` and ``bi.super()`` read the frame's cell like ``super()`` but do not give one."""
    from towel.unification.semantic_safety import frame_aliases, requires_original_frame

    module = ast.parse(
        "import builtins as bi\nfrom builtins import super as sup\ns = super\nt = s\n"
        "def f(self):\n    a = s().x\n    b = t(*()).x\n    c = bi.super().x\n    d = sup().x\n"
        "    e = s(C, self).x\n    f = super().x\n"
    )
    aliases = frame_aliases(module)
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)
    verdicts = [requires_original_frame([statement], aliases) for statement in function.body]
    assert verdicts == [True, True, True, True, False, False]


# -- the typed case ------------------------------------------------------------------

TYPED = """
from __future__ import annotations


class Base:
    def label(self, total: int) -> str:
        return f"base:{total}"


class Ledger(Base):
    def __init__(self) -> None:
        self.entries: list[int] = []

    def credit(self, amount: int) -> str:
        print("credit", amount)
        self.entries.append(amount)
        text = super().label(sum(self.entries))
        return text.upper()

    def refund(self, amount: int) -> str:
        print("refund", amount)
        self.entries.append(amount)
        text = super().label(sum(self.entries))
        return text.lower()


class Audited(Ledger):
    def label(self, total: int) -> str:
        return "audited"
"""


@requires_checkers
def test_a_super_helper_passes_mypy_strict_and_pyright_strict(tmp_path: Path) -> None:
    """The typed run writes an annotated class-private helper holding ``super()``, and both check it."""
    from towel.type_inference import MypyInferrer

    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    (tmp_path / "ledger.py").write_text(TYPED.lstrip())
    for tool in ("mypy", "pyright"):
        assert _strict_errors(tool, tmp_path / "ledger.py") == []
    driver = (
        "from ledger import Audited, Ledger\n"
        "for ledger in (Ledger(), Audited()):\n"
        "    print(ledger.credit(3), ledger.refund(1), ledger.label(0))\n"
    )
    before = _run(tmp_path, driver, tmp_path)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _, applied, _ = engine.refactor_to_fixed_point(str(tmp_path / "ledger.py"))
    finally:
        oracle.close()
    assert applied == 1
    source = (tmp_path / "ledger.py").read_text()
    (helper,) = _helpers_in(source, "Ledger")
    assert is_class_private(helper.name) and helper.returns is not None, source
    assert "super().label(" in ast.unparse(helper), source
    assert _run(tmp_path, driver, tmp_path) == before
    for tool in ("mypy", "pyright"):
        assert _strict_errors(tool, tmp_path / "ledger.py") == [], (tool, source)
