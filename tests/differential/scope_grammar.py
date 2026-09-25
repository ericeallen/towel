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

"""A seeded family of near-duplicates whose block may hold its function's only binding of a name.

The round-4 audit's P1-04: Python makes a name local to a function if any
of its code binds it, and a block that held the function's only binding
took the name's locality into the helper, so the function's other reads of
it found a module name, a builtin or a closure instead. The main grammar
(:mod:`tests.differential.grammar`) never reads a name outside the block
that only the block binds, so it could not reach this.

Each case writes two sites (three, one time in six), ``f1`` and ``f2``, as
near-duplicates with renamed parameters. A name ``N`` is bound in the shared
block by one of the binding constructs, and read outside it in one of the
places code can read a function's name: before the block on an early path or
under a ``try``, after it, in a nested function or lambda, in a comprehension
or class body, in an ``except`` handler around code before the block, or by
a nested function's ``nonlocal``. What ``N`` would otherwise be is drawn too:
a module name, a builtin, a variable of an enclosing function, or nothing.
Controls come from the same draws: another binding of ``N`` outside the
block, a ``global`` declaration, or no read outside the block at all, so the
family also shows the extractions that stay sound.

:func:`generate_scope_case` is a pure function of its seed, like
:func:`tests.differential.grammar.generate_case`, and returns the same
:class:`tests.differential.cases.Case`, so the runner, the observer and the
fixture exporter treat both families alike. Cases are untyped.
"""

from __future__ import annotations

import random
from typing import Dict, List, Sequence, Tuple

from tests.differential.cases import Calls, Case, Probe, Value

FAMILY = "scope"

_GLOBAL_NAME = "total"
_BUILTIN_NAMES = ("len", "id", "sum", "sorted")
_BINDERS = (
    "assign",
    "augmented",
    "annotated",
    "bare_annotation",
    "delete",
    "for",
    "with",
    "except_as",
    "import",
    "walrus",
    "match",
    "def",
    "class",
    "tuple",
)
_READS = (
    "before_branch",
    "before_caught",
    "after_caught",
    "nested_function",
    "lambda",
    "comprehension",
    "class_body",
    "handler",
    "nonlocal",
    "none",
)
_OUTER = ("global", "builtin", "closure", "nothing")
_CONTROLS = ("none", "none", "none", "bound_outside", "declared_global")
_PARAMETER_SETS = (("a", "b"), ("x", "y"), ("n", "m"))
INPUTS = "[(2, [1, 2]), (-1, [3]), (0, []), (5, [4, 0, 9])]"
"""The argument tuples ``(a, b)`` every site is called with: the early paths take ``a < 0``."""


def case_name(seed: int) -> str:
    """The name of the case ``seed`` draws: ``scope_u0042``."""
    return f"{FAMILY}_u{seed:04d}"


def generate_scope_case(seed: int, *, typed: bool = False) -> Case:
    """The case ``seed`` draws; ``typed`` is accepted for the fuzz driver and must be false."""
    if typed:
        raise ValueError("the scope family draws untyped cases only")
    return _ScopeGenerator(seed).build()


def _indent(lines: Sequence[str], by: str = "    ") -> List[str]:
    return [by + line if line else line for line in lines]


class _ScopeGenerator:
    """One draw of the family. Its state lives only for one :meth:`build`."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.r = random.Random(seed)
        self.features: set[str] = set()

    def build(self) -> Case:
        r = self.r
        outer = r.choice(_OUTER)
        name = r.choice(_BUILTIN_NAMES) if outer == "builtin" else _GLOBAL_NAME
        binder = r.choice(_BINDERS)
        read = r.choice(_READS)
        control = r.choice(_CONTROLS)
        if control == "declared_global" and (
            outer == "closure" or read == "nonlocal" or binder in ("annotated", "bare_annotation")
        ):
            # A global declaration does not compile beside a nonlocal one or an annotation.
            control = "none"
        prefix_differs = r.random() < 0.4
        nsites = 3 if r.random() < 1 / 6 else 2
        self.features |= {
            f"outer_{outer}",
            f"binder_{binder}",
            f"read_{read}",
            f"control_{control}",
            f"sites_{nsites}",
        }
        if prefix_differs:
            self.features.add("prefix_differs")
        block = self.block(binder, name)
        sites = [
            self.site(index, name, block, read, control, outer, prefix_differs)
            for index in range(nsites)
        ]
        module: List[str] = ["import contextlib", ""]
        if outer == "global":
            module += [f"{name} = 'module {name}'", ""]
        module += ["", *("\n".join(site) + "\n\n" for site in sites)]
        source = "\n".join(module).rstrip("\n") + "\n"
        files = (("pyproject.toml", '[project]\nname = "p"\nversion = "0"\n'), ("m.py", source))
        probes: List[Probe] = [Calls(f"M.f{index + 1}", INPUTS) for index in range(nsites)]
        if outer == "global":
            probes.append(Value(f"M.{name}"))
        return Case(
            name=case_name(self.seed),
            seed=self.seed,
            typed=False,
            layout="single",
            features=frozenset(self.features | {FAMILY}),
            files=files,
            modules=(("M", "m"),),
            probes=tuple(probes),
        )

    # -- the block ------------------------------------------------------------

    def block(self, binder: str, name: str) -> List[str]:
        """The shared block, its binding of ``name`` first; ``@a@`` and ``@b@`` are the parameters."""
        r = self.r
        binding: Dict[str, List[str]] = {
            "assign": [f"{name} = @a@ * 2"],
            "augmented": [
                "try:",
                f"    {name} += @a@",
                "except UnboundLocalError:",
                "    print('augmented', @a@)",
            ],
            "annotated": [f"{name}: int = @a@ + 1"],
            "bare_annotation": [f"{name}: int"],
            "delete": [
                "try:",
                f"    del {name}",
                "except UnboundLocalError:",
                "    print('deleted', @a@)",
            ],
            "for": [f"for {name} in @b@:", f"    print('item', {name})"],
            "with": [f"with contextlib.nullcontext(@a@) as {name}:", f"    print('with', {name})"],
            "except_as": [
                "try:",
                "    raise ValueError(@a@)",
                f"except ValueError as {name}:",
                f"    print('caught', {name})",
            ],
            "import": [f"import contextlib as {name}", f"print('module', {name}.__name__)"],
            "walrus": [f"print('walrus', ({name} := @a@ + 3))"],
            "match": ["match @b@:", f"    case [{name}, *_]:", f"        print('first', {name})"],
            "def": [f"def {name}(v=@a@):", "    return v", f"print('def', {name}())"],
            "class": [f"class {name}:", "    kind = 'class'", f"print('class', {name}.kind)"],
            "tuple": [f"_first, {name} = @a@, @b@[:1]", "print('tuple', _first)"],
        }
        lines = list(binding[binder])
        # Nothing here reads a builtin: the name the block binds may spell one.
        tail = [
            "print('block', @a@, @b@[:2])",
            f"print('twice', @a@ * {r.randint(2, 9)})",
        ]
        if r.random() < 0.5:
            tail.append(f"@b@.append(@a@ + {r.randint(1, 5)})")
        return lines + tail

    # -- one site ---------------------------------------------------------------

    def site(
        self,
        index: int,
        name: str,
        block: List[str],
        read: str,
        control: str,
        outer: str,
        prefix_differs: bool,
    ) -> List[str]:
        a, b = _PARAMETER_SETS[index % len(_PARAMETER_SETS)]
        tag = f"s{index}" if prefix_differs else "s"

        def filled(lines: Sequence[str]) -> List[str]:
            return [line.replace("@a@", a).replace("@b@", b) for line in lines]

        before, after = self.reads(read, name, a, tag)
        head: List[str] = []
        if control == "declared_global":
            head.append(f"global {name}")
        elif control == "bound_outside":
            head.append(f"{name} = {a} - 1")
        body = head + before + filled(block) + after + [f"return ('done', {a})"]
        function = f"f{index + 1}"
        if outer != "closure":
            return [f"def {function}({a}, {b}):", *_indent(body)]
        return [
            f"def {function}({a}, {b}):",
            f"    {name} = 'closure {name}'",
            "",
            "    def inner():",
            *_indent(body, "        "),
            "",
            f"    return inner(), {name}",
        ]

    @staticmethod
    def reads(read: str, name: str, a: str, tag: str) -> Tuple[List[str], List[str]]:
        """The code reading ``name`` outside the block: (what goes before it, what goes after)."""
        caught = [
            "try:",
            f"    seen = {name}",
            "except (UnboundLocalError, NameError) as error:",
            "    seen = type(error).__name__",
            f"print('{tag}', 'seen', seen)",
        ]
        if read == "before_branch":
            return [f"if {a} < 0:", f"    return ('early', {name})"], []
        if read == "before_caught":
            return caught, []
        if read == "after_caught":
            return [], caught
        if read == "nested_function":
            return [
                "def peek():",
                f"    return {name}",
                "",
                f"if {a} < 0:",
                "    return ('peek', peek())",
            ], []
        if read == "lambda":
            return [f"peek = lambda: {name}", f"if {a} < 0:", "    return ('peek', peek())"], []
        if read == "comprehension":
            return [f"if {a} < 0:", f"    return ('seen', [{name} for _ in range(1)])"], []
        if read == "class_body":
            return [
                f"if {a} < 0:",
                "    class Holder:",
                f"        value = {name}",
                "",
                "    return ('held', Holder.value)",
            ], []
        if read == "handler":
            return [
                "try:",
                f"    if {a} < 0:",
                f"        raise KeyError('{tag}')",
                "except KeyError:",
                f"    return ('handled', {name})",
            ], []
        if read == "nonlocal":
            return [
                "def reset():",
                f"    nonlocal {name}",
                f"    {name} = 'reset'",
                "",
                f"if {a} < 0:",
                "    reset()",
                f"    return ('reset', {a})",
            ], []
        return [f"print('{tag}', 'start', {a})"], []
