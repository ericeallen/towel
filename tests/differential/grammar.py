"""A seeded grammar of near-duplicate blocks.

One template block is drawn from a grammar of about 26 statement forms and
20 expression forms. Two sites instantiate it (three, one time in seven),
with consistently renamed locals and parameters, and differ at up to three
holes, each filled with a different expression: constants, side-effecting
``tr()`` calls, short-circuits, raising subscripts, ``//`` by zero, set and
starred displays, lambda defaults. The enclosing scope varies (function,
method, a method reading ``self``, static, classmethod, closure), and so do
the block's position (at the top, or inside ``if``, ``for``, ``while``,
``try`` or ``with``), the statements around it and the layout (package,
single file, or two modules sharing a star-imported prelude).

:func:`generate_case` is a pure function of its seed. It reproduces the
round-3 audit's final generator draw for draw, so seed 898 is the audit's
``gram_u0898`` and typed seed 11094 its ``gram_t11094``: the 1300 cases the
audit kept from it (untyped seeds 400 to 899 and 1000 to 1299, typed seeds
10400 to 10599 and 11000 to 11299) come out byte for byte. Keep the order of
every draw when changing it, or say in the change that seeds have moved.
"""

from __future__ import annotations

import random
import re
from typing import Callable, Dict, List, Literal, Optional, Sequence, Set, Tuple

from tests.differential.cases import Calls, Case, Checker, Layout, Probe, Statement, Value

Kind = Literal["int", "list", "str"]
Scope = Dict[str, Kind]
"""The placeholder names bound so far, by what they hold."""

ScopeKind = Literal["func", "method", "nested", "static", "classmethod", "method_self"]
Position = Literal["top", "if", "for", "try", "with", "while"]

PRELUDE = """\
LOG = []
G = 5


def tr(tag, v):
    print("tr", tag, repr(v))
    LOG.append(tag)
    return v


class Box:
    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return f"Box({self.v!r})"

    def __eq__(self, other):
        return isinstance(other, Box) and self.v == other.v


class Ctx:
    def __init__(self, tag):
        self.tag = tag

    def __enter__(self):
        print("enter", self.tag)
        return self.tag

    def __exit__(self, et, ev, tb):
        print("exit", self.tag, et.__name__ if et else None)
        return False


def bump():
    global G
    G += 1
    return G
"""
"""The definitions every generated module starts with, or star-imports."""

PRELUDE_TYPED = """\
from typing import Any

LOG: list[str] = []
G = 5


def tr(tag: str, v: Any) -> Any:
    print("tr", tag, repr(v))
    LOG.append(tag)
    return v


class Box:
    def __init__(self, v: int) -> None:
        self.v = v

    def __repr__(self) -> str:
        return f"Box({self.v!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Box) and self.v == other.v


class Ctx:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> str:
        print("enter", self.tag)
        return self.tag

    def __exit__(self, et: Any, ev: Any, tb: Any) -> bool:
        print("exit", self.tag, et.__name__ if et else None)
        return False


def bump() -> int:
    global G
    G += 1
    return G
"""

INPUTS = (
    "[(0, [], {}, M.Box(0)), (1, [1, 2, 3], {'k': 1}, M.Box(1)),"
    " (3, [0, 5, -1], {'k': 0, 'j': 2}, M.Box(-2)),"
    " (-2, [4], {'j': 7}, M.Box(3)), (2, [1, 1], {'k': 5}, M.Box(2)),"
    " (7, [9, 8, 7, 6], {'k': -3}, M.Box(0))]"
)
"""The argument tuples ``(a, b, c, o)`` every site is called with; ``M`` names the prelude's module."""

PYPROJECT = '[project]\nname = "pkg"\nversion = "0"\nrequires-python = ">=3.11"\n'
_MYPY = "\n[tool.mypy]\nstrict = true\n"
_PYRIGHT = '\n[tool.pyright]\ntypeCheckingMode = "strict"\n'
PYPROJECTS: Dict[Optional[Checker], str] = {
    None: PYPROJECT,
    "mypy": PYPROJECT + _MYPY,
    "pyright": PYPROJECT + _PYRIGHT,
    "both": PYPROJECT + _MYPY + _PYRIGHT,
}

_LAYOUT_BY_SEED: Tuple[Layout, ...] = ("package", "package", "single", "multi", "multi")
_CHECKER_BY_SEED: Tuple[Checker, ...] = ("mypy", "pyright", "mypy", "both")
_PARAMETER_SETS = (("a", "b", "c", "o"), ("x", "y", "z", "w"), ("a", "b", "c", "o"))
_PLACEHOLDER = re.compile(r"@([VPH]):(\w+)@")
_COMPOUND_STARTS = ("global", "if ", "for ", "while ", "with ", "try", "match ")
_SCOPE_KINDS: Tuple[ScopeKind, ...] = (
    ("func",) * 5
    + ("method",) * 3
    + (
        "nested",
        "static",
        "classmethod",
        "method_self",
    )
)
"""The functions that host the sites, drawn with these odds."""
_POSITIONS: Tuple[Position, ...] = ("top",) * 5 + ("if", "for", "try", "with", "while")
"""Where the block stands in its function, drawn with these odds."""


def case_name(seed: int, *, typed: bool) -> str:
    """The name the audit gave the case of ``seed``: ``gram_u0898``, ``gram_t11094``."""
    return f"gram_{'t' if typed else 'u'}{seed:04d}"


def generate_case(seed: int, *, typed: bool = False) -> Case:
    """The case ``seed`` draws: untyped, or with annotations and a strict checker configured."""
    return _Generator(seed, typed).build()


def _indent(lines: Sequence[str], by: str = "    ") -> List[str]:
    return [by + line for line in lines]


class _Generator:
    """One draw of the grammar. Its state lives only for one :meth:`build`."""

    def __init__(self, seed: int, typed: bool) -> None:
        self.seed = seed
        self.typed = typed
        self.layout: Layout = _LAYOUT_BY_SEED[seed % 5]
        self.checker: Checker = _CHECKER_BY_SEED[seed % 4]
        self.r = random.Random(seed)
        self.holes: List[Tuple[str, str]] = []
        self.bound = 0
        self.features: Set[str] = set()
        self.last_was_simple = False

    # ---- names -------------------------------------------------------------

    def fresh(self) -> str:
        self.bound += 1
        return f"@V:v{self.bound}@"

    def hole(self, draw: Callable[[], str]) -> str:
        """A placeholder the sites fill with two different draws, or the draw where they agree."""
        first = draw()
        second = draw()
        tries = 0
        while second == first and tries < 10:
            second = draw()
            tries += 1
        if first == second:
            return first
        self.holes.append((first, second))
        self.features.add("hole")
        return f"@H:{len(self.holes) - 1}@"

    # ---- expressions -------------------------------------------------------

    def int_atom(self, scope: Scope) -> str:
        r = self.r
        choices: List[Callable[[], str]] = [
            lambda: str(r.choice([1, 2, 3, 4, 5, 6, 7, 8, 9, -1, -2, 0])),
            lambda: "@P:a@",
            lambda: "len(@P:b@)",
            lambda: "@P:o@.v",
            lambda: "@P:c@.get('k', 0)",
            lambda: "G",
        ]
        ints = [name for name, kind in scope.items() if kind == "int"]
        if ints:
            choices += [lambda: r.choice(ints)] * 3
        return r.choice(choices)()

    def int_expr(self, scope: Scope, depth: int = 0) -> str:
        r = self.r
        if depth >= 2 or r.random() < 0.35:
            return self.int_atom(scope)
        k = r.randint(0, 17)

        def e() -> str:
            return self.int_expr(scope, depth + 1)

        if k == 0:
            self.features.add("tr")
            return f"tr('{r.choice('xyzpq')}{r.randint(0, 9)}', {e()})"
        if k == 1:
            return f"({e()} + {e()})"
        if k == 2:
            return f"({e()} * {e()})"
        if k == 3:
            self.features.add("zerodiv")
            return f"({e()} // ({e()} % 5))"
        if k == 4:
            self.features.add("ternary")
            return f"({e()} if {self.cond(scope, depth + 1)} else {e()})"
        if k == 5:
            self.features.add("shortcircuit")
            return f"({e()} and {e()})"
        if k == 6:
            self.features.add("shortcircuit")
            return f"({e()} or {e()})"
        if k == 7:
            self.features.add("indexerror")
            return f"@P:b@[{e()} % 4]"
        if k == 8:
            self.features.add("keyerror")
            return "@P:c@['k']"
        if k == 9:
            return f"max({e()}, {e()})"
        if k == 10:
            return f"abs({e()})"
        if k == 11:
            self.features.add("lambda_call")
            return f"(lambda q: q * 2 + 1)({e()})"
        if k == 12:
            self.features.add("genexp")
            return f"sum(q for q in @P:b@ if q > {e()})"
        if k == 13:
            return f"(sum(@P:b@) - {e()})"
        if k == 14:
            self.features.add("bump")
            return "bump()"
        if k == 15:
            self.features.add("fstring_int")
            return f'len(f"{{{self.int_atom(scope)}}}-{{@P:a@}}")'
        if k == 16:
            self.features.add("chained_cmp")
            return f"(1 if {e()} < {e()} <= {e()} else 0)"
        # k == 17: each rarer form is tried in turn with even odds.
        if r.random() < 0.5:
            self.features.add("set_display")
            return f"len({{{e()}, {e()}}})"
        if r.random() < 0.5:
            self.features.add("starred_display")
            return f"len([*@P:b@, {e()}])"
        if r.random() < 0.5:
            self.features.add("lambda_default")
            return f"(lambda q={e()}: q + 1)()"
        return f"({e()} % 7)"

    def cond(self, scope: Scope, depth: int = 0) -> str:
        r = self.r

        def e() -> str:
            return self.int_expr(scope, depth + 1)

        k = r.randint(0, 6)
        if k == 0:
            return f"{e()} > {e()}"
        if k == 1:
            return f"{e()} == {e()}"
        if k == 2:
            return f"{e()} in @P:b@"
        if k == 3:
            return f"not {e()}"
        if k == 4:
            self.features.add("isinstance")
            return f"isinstance({e()}, int)"
        if k == 5:
            self.features.add("shortcircuit")
            return f"({e()} > 0 and {e()} < 5)"
        return f"{e()} != 0"

    def hexpr(self, scope: Scope, depth: int = 0) -> str:
        """An expression that may be a hole."""
        if self.r.random() < 0.25 and len(self.holes) < 3:
            return self.hole(lambda: self.int_expr(scope, depth))
        return self.int_expr(scope, depth)

    # ---- statements --------------------------------------------------------

    def stmts(self, scope: Scope, n: int, depth: int, in_loop: bool = False) -> List[str]:
        out: List[str] = []
        for _ in range(n):
            new = self.stmt(scope, depth, in_loop)
            simple = (
                len(new) == 1
                and not new[0].rstrip().endswith(":")
                and not new[0].startswith(_COMPOUND_STARTS)
            )
            if simple and out and self.r.random() < 0.06 and self.last_was_simple:
                self.features.add("semicolon")
                out[-1] = out[-1] + "; " + new[0]
            else:
                out += new
            self.last_was_simple = simple
        return out

    def stmt(self, scope: Scope, depth: int, in_loop: bool) -> List[str]:
        r = self.r
        k = r.randint(0, 25 if depth < 1 else 12)

        def expr() -> str:
            return self.hexpr(scope)

        if k <= 2:
            v = self.fresh()
            rhs = expr()
            scope[v] = "int"
            if self.typed and r.random() < 0.3:
                self.features.add("annassign")
                return [f"{v}: int = {rhs}"]
            return [f"{v} = {rhs}"]
        if k == 3:
            ints = [name for name, kind in scope.items() if kind == "int" and name.startswith("@V")]
            if ints:
                self.features.add("augassign")
                return [f"{r.choice(ints)} {r.choice(['+=', '-=', '*='])} {expr()}"]
            return [f'print("p", {expr()})']
        if k in (4, 5):
            self.features.add("print")
            return [f'print("p{r.randint(0, 9)}", {expr()}, {expr()})']
        if k == 6:
            self.features.add("mutate_arg")
            return [f"@P:b@.append({expr()})"]
        if k == 7:
            self.features.add("LOG")
            return [f"LOG.append(str({expr()}))"]
        if k == 8:
            self.features.add("attr_store")
            return [f"@P:o@.v = {expr()}"]
        if k == 9:
            self.features.add("subscript_store")
            return [f"@P:c@['j'] = {expr()}"]
        if k == 10:
            self.features.add("assert")
            return [f'assert {self.cond(scope)}, "assert failed " + str({expr()})']
        if k == 11:
            self.features.add("raise")
            return [f"if {self.cond(scope)}:", f'    raise ValueError("bad " + str({expr()}))']
        if k == 12:
            v1, v2 = self.fresh(), self.fresh()
            rhs = f"{expr()}, {expr()}"
            scope[v1] = scope[v2] = "int"
            self.features.add("tuple_unpack")
            return [f"{v1}, {v2} = {rhs}"]
        if k in (13, 14):
            return self.if_stmt(scope, depth, in_loop)
        if k in (15, 16):
            return self.for_stmt(scope, depth)
        if k == 17:
            self.features.add("while")
            n = self.fresh()
            scope[n] = "int"
            body = self.stmts(dict(scope), r.randint(1, 2), depth + 1, True)
            return [f"{n} = 0", f"while {n} < 3:"] + _indent(body + [f"{n} += 1"])
        if k in (18, 19):
            return self.try_stmt(scope, depth, in_loop)
        if k == 20:
            self.features.add("with")
            cv = self.fresh()
            inner = dict(scope)
            inner[cv] = "str"
            body = self.stmts(inner, r.randint(1, 2), depth + 1, in_loop)
            return [f'with Ctx("w{r.randint(0, 9)}") as {cv}:'] + _indent(body)
        if k == 21:
            self.features.add("match")
            first, second, rest = dict(scope), dict(scope), dict(scope)
            return (
                [f"match {self.int_expr(scope)}:", "    case 0:"]
                + _indent(self.stmts(first, 1, depth + 1, in_loop), "        ")
                + ["    case 1 | 2:"]
                + _indent(self.stmts(second, 1, depth + 1, in_loop), "        ")
                + ["    case _:"]
                + _indent(self.stmts(rest, 1, depth + 1, in_loop), "        ")
            )
        if k == 22:
            self.features.add("comprehension")
            v = self.fresh()
            scope[v] = "list"
            return [
                f"{v} = [q * {expr()} for q in @P:b@ if q > {self.int_expr(scope)}]",
                f'print("comp", {v})',
            ]
        if k == 23:
            self.features.add("import")
            v = self.fresh()
            scope[v] = "int"
            return ["import math", f"{v} = math.floor({expr()} / 2)"]
        if k == 24:
            self.features.add("walrus")
            v, w = self.fresh(), self.fresh()
            rhs = expr()
            scope[v] = scope[w] = "int"
            return [f"{v} = ({w} := {rhs}) + 1"]
        # k == 25
        if r.random() < 0.4:
            bound_before = [n for n, kind in scope.items() if kind == "int" and n.startswith("@V")]
            if bound_before:
                v = r.choice(bound_before)
                self.features.add("for_rebinds_existing")
                return [f"for {v} in @P:b@:", f'    print("it", {v})', f'print("last", {v})']
        self.features.add("nested_def")
        g = self.fresh()
        v = self.fresh()
        scope[v] = "int"
        return [f"def {g}(q):", f"    return q + {self.int_expr(scope)}", f"{v} = {g}({expr()})"]

    def if_stmt(self, scope: Scope, depth: int, in_loop: bool) -> List[str]:
        r = self.r
        self.features.add("if")
        test = self.cond(scope)
        taken = dict(scope)
        body = self.stmts(taken, r.randint(1, 2), depth + 1, in_loop)
        lines = [f"if {test}:"] + _indent(body)
        if r.random() < 0.3:
            self.features.add("elif")
            lines += [f"elif {self.cond(scope)}:"] + _indent(
                self.stmts(dict(scope), 1, depth + 1, in_loop)
            )
        if r.random() < 0.6:
            other = dict(scope)
            lines += ["else:"] + _indent(self.stmts(other, r.randint(1, 2), depth + 1, in_loop))
            # A name bound on both branches is bound after the statement. The
            # branches draw fresh names, so in practice only the enclosing
            # scope's names are common; sorted keeps the draw independent of
            # the interpreter's string hashing either way.
            for name in sorted(set(taken) & set(other)):
                scope.setdefault(name, taken[name])
        return lines

    def for_stmt(self, scope: Scope, depth: int) -> List[str]:
        r = self.r
        self.features.add("for")
        target = self.fresh()
        # Every iterable is drawn, and so consumes its draws, before one is chosen.
        iterable = r.choice(["@P:b@", f"range({self.int_expr(scope)} % 4)", "list(@P:c@.values())"])
        inner = dict(scope)
        inner[target] = "int"
        body = self.stmts(inner, r.randint(1, 2), depth + 1, True)
        if r.random() < 0.4:
            self.features.add("break_continue")
            body += [f"if {self.cond(inner)}:", "    " + r.choice(["break", "continue"])]
        lines = [f"for {target} in {iterable}:"] + _indent(body)
        if r.random() < 0.25:
            self.features.add("for_else")
            lines += ["else:"] + _indent([f'print("for-else", {self.int_expr(scope)})'])
        return lines

    def try_stmt(self, scope: Scope, depth: int, in_loop: bool) -> List[str]:
        r = self.r
        self.features.add("try")
        caught = self.fresh()
        body = self.stmts(dict(scope), r.randint(1, 2), depth + 1, in_loop)
        lines = ["try:"] + _indent(body)
        lines += [
            f"except (ZeroDivisionError, IndexError, KeyError) as {caught}:",
            f'    print("caught", type({caught}).__name__, {caught})',
        ]
        if r.random() < 0.3:
            self.features.add("try_else")
            lines += ["else:", f'    print("else", {self.int_expr(scope)})']
        if r.random() < 0.4:
            self.features.add("finally")
            lines += ["finally:", f'    print("finally", {self.int_expr(scope)})']
        return lines

    # ---- whole case ---------------------------------------------------------

    def build(self) -> Case:
        r = self.r
        scope_kind = r.choice(_SCOPE_KINDS)
        position = r.choice(_POSITIONS)
        nsites = 3 if r.random() < 0.15 else 2
        base_scope: Scope = {}
        prefix: List[str] = []
        if r.random() < 0.6:
            v = self.fresh()
            base_scope[v] = "int"
            prefix.append(f"{v} = {self.int_expr({})}")
        if r.random() < 0.3:
            prefix.append(f'print("prefix", {self.int_expr(base_scope)})')
        scope = dict(base_scope)
        block = self.stmts(scope, r.randint(3, 5), 0)
        bound_after = [
            name for name in scope if name not in base_scope and scope[name] in ("int", "list")
        ]
        suffix: List[str] = []
        if r.random() < 0.35:
            block.append(f"return {self.hexpr(scope)}")
        else:
            if bound_after and r.random() < 0.8:
                picks = r.sample(bound_after, min(len(bound_after), r.randint(1, 2)))
                suffix.append(f'print("after", {", ".join(picks)})')
            suffix.append(f"return {self.int_expr(scope) if r.random() < 0.5 else 'None'}")
        body = prefix + self.placed(block, position) + suffix
        self.features.add(f"scope_{scope_kind}")
        self.features.add(f"sites_{nsites}")
        sites = [self.site(index, body, scope_kind) for index in range(nsites)]
        if self.layout == "multi" and scope_kind in ("func", "nested"):
            return self.multi_module_case(sites)
        return self.one_module_case(sites, scope_kind)

    def placed(self, lines: List[str], position: Position) -> List[str]:
        """The block where ``position`` puts it inside its function."""
        if position == "top":
            return lines
        self.features.add(f"pos_{position}")
        if position == "if":
            return ["if @P:a@ > -10:"] + _indent(lines)
        if position == "for":
            return ["for _i in range(1):"] + _indent(lines)
        if position == "try":
            return ["try:"] + _indent(lines) + ["except RuntimeError:", "    pass"]
        if position == "with":
            return ['with Ctx("outer"):'] + _indent(lines)
        return ["while True:"] + _indent(lines) + ["    break"]

    def site(self, index: int, body: List[str], scope_kind: ScopeKind) -> List[str]:
        """The source lines of site ``index``: ``f1``, ``f2`` or ``f3``, as ``scope_kind`` hosts it."""
        r = self.r
        names = _PARAMETER_SETS[index if r.random() < 0.7 else 0]
        renamed = r.random() < 0.5 and index > 0

        def fill(match: re.Match[str]) -> str:
            kind, key = match.group(1), match.group(2)
            if kind == "V":
                return f"{key}_{index}" if renamed else key
            if kind == "P":
                return names["abco".index(key)]
            first, second = self.holes[int(key)]
            return first if index == 0 else second

        def filled(line: str, passes: int = 0) -> str:
            out = _PLACEHOLDER.sub(fill, line)
            if "@" in out and passes < 5:
                return filled(out, passes + 1)
            return out

        lines = [filled(line) for line in body]
        if index == 1 and r.random() < 0.3:
            lines = [f'print("site1-prefix", {names[0]})'] + lines
        a, b, c, o = names
        typed = self.typed
        signature = (
            f"{a}: int, {b}: list[int], {c}: dict[str, int], {o}: Box"
            if typed
            else (f"{a}, {b}, {c}, {o}")
        )
        returns = " -> Any" if typed else ""
        name = f"f{index + 1}"
        if scope_kind == "func":
            return [f"def {name}({signature}){returns}:"] + _indent(lines)
        if scope_kind in ("method", "method_self"):
            counting = (
                ["self.count += 1", 'print("self", self.count)']
                if scope_kind == "method_self"
                else []
            )
            return [f"    def {name}(self, {signature}){returns}:"] + _indent(
                counting + lines, "        "
            )
        if scope_kind == "static":
            return ["    @staticmethod", f"    def {name}({signature}){returns}:"] + _indent(
                lines, "        "
            )
        if scope_kind == "classmethod":
            return ["    @classmethod", f"    def {name}(cls, {signature}){returns}:"] + _indent(
                ['print("cls", cls.__name__)'] + lines, "        "
            )
        return (
            [
                f"def {name}({signature}){returns}:",
                f"    k{index} = {index + 2}",
                f"    def inner(){returns}:",
                f'        print("cell", k{index})',
            ]
            + _indent(lines, "        ")
            + ["    return inner()"]
        )

    def prelude(self) -> str:
        return PRELUDE_TYPED if self.typed else PRELUDE

    def pyproject(self) -> str:
        return PYPROJECTS[self.checker if self.typed else None]

    def case(
        self,
        layout: Layout,
        files: Dict[str, str],
        modules: Tuple[Tuple[str, str], ...],
        probes: List[Probe],
    ) -> Case:
        return Case(
            name=case_name(self.seed, typed=self.typed),
            seed=self.seed,
            typed=self.typed,
            layout=layout,
            features=frozenset(self.features | ({layout} if layout != "package" else set())),
            files=tuple(files.items()),
            modules=modules,
            probes=tuple(probes),
            checker=self.checker if self.typed else None,
        )

    def multi_module_case(self, sites: List[List[str]]) -> Case:
        """``f1`` in ``pkg/a.py``, the others in ``pkg/b.py``, which imports ``a``."""
        head = "from typing import Any\n" if self.typed else ""
        a_source = head + "from .common import *\n\n\n" + "\n".join(sites[0]) + "\n"
        b_source = (
            head
            + "from . import a\nfrom .common import *\n\n\n"
            + "\n\n\n".join("\n".join(site) for site in sites[1:])
            + "\n"
        )
        files = {
            "pyproject.toml": self.pyproject(),
            "pkg/__init__.py": "",
            "pkg/common.py": self.prelude(),
            "pkg/a.py": a_source,
            "pkg/b.py": b_source,
        }
        inputs = INPUTS.replace("M.Box", "C.Box")
        names = [f"f{index + 1}" for index in range(len(sites))]
        probes: List[Probe] = [
            Calls(f"{'A' if name == 'f1' else 'B'}.{name}", inputs) for name in names
        ]
        probes += [
            Value("C.LOG[-50:]"),
            Value("C.G"),
            Value("(A.G, B.G)"),
            Statement("B.G = 100"),
            Calls(f"B.{names[1]}", inputs),
        ]
        modules = (("C", "pkg.common"), ("A", "pkg.a"), ("B", "pkg.b"))
        return self.case("multi", files, modules, probes)

    def one_module_case(self, sites: List[List[str]], scope_kind: ScopeKind) -> Case:
        """Every site in one module: ``pkg/m.py``, or a lone ``m.py``."""
        if scope_kind in ("func", "nested"):
            body = "\n\n\n".join("\n".join(site) for site in sites)
        else:
            header = [
                "class K:",
                "    def __init__(self)" + (" -> None" if self.typed else "") + ":",
                "        self.count = 0",
                "",
            ]
            body = "\n".join(header) + "\n" + "\n\n".join("\n".join(site) for site in sites)
        source = self.prelude() + "\n\n" + body + "\n"
        single = self.layout == "single"
        files = {"pyproject.toml": self.pyproject()}
        if single:
            files["m.py"] = source
        else:
            files["pkg/__init__.py"] = ""
            files["pkg/m.py"] = source
        receiver = {
            "func": "M",
            "nested": "M",
            "method": "M.K()",
            "method_self": "M.K()",
            "static": "M.K",
            "classmethod": "M.K",
        }[scope_kind]
        probes: List[Probe] = [
            Calls(f"{receiver}.f{index + 1}", INPUTS) for index in range(len(sites))
        ]
        probes += [Value("M.LOG[-50:]"), Value("M.G")]
        module = "m" if single else "pkg.m"
        return self.case("single" if single else "package", files, (("M", module),), probes)
