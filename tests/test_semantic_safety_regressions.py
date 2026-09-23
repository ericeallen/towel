"""Execute original and rewritten programs to test extraction semantics."""

import ast
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Callable, Iterator, cast
import unittest

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import nested_bindings_escape
from towel.unification.import_graph import would_create_import_cycle
from towel.unification.import_graph import ImportGraphCache
from towel.cli import _find_extracted_helpers


class DistinctOperators:
    """Python operators need not obey arithmetic identities."""

    def __sub__(self, other: int) -> int:
        return 100 - other

    def __add__(self, other: int) -> int:
        return 200 + other


class TestSemanticSafetyRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="towel-semantics-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, body: str, parameters: str = "") -> str:
        return "".join(f"def {name}({parameters}):\n{body}" for name in ("first", "second"))

    def rewrite(self, source: str) -> str:
        path = self.root / "example.py"
        path.write_text(source, encoding="utf-8")
        engine = UnificationRefactorEngine()
        with contextlib.redirect_stdout(io.StringIO()):
            proposals = engine.analyze_files([str(path)], progress="none")
        self.assertTrue(proposals, "Fixture must exercise a real extraction")
        result = engine.apply_refactoring(str(path), proposals[0])
        compile(result, str(path), "exec")
        return result

    def call(self, source: str, *args: object) -> object:
        namespace: dict[str, object] = {}
        exec(source, namespace)
        return cast(Callable[..., object], namespace["first"])(*args)

    def assert_rejected(self, source: str) -> None:
        path = self.root / "example.py"
        path.write_text(source, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            proposals = UnificationRefactorEngine().analyze_files([str(path)], progress="none")
        self.assertEqual(proposals, [])

    def test_list_rebinding_does_not_mutate_alias(self) -> None:
        source = self.source("    x = [1]\n    alias = x\n    x = x + [2]\n    return alias, x\n")
        rewritten = self.rewrite(source)
        self.assertEqual(self.call(source), ([1], [1, 2]))
        self.assertEqual(self.call(rewritten), self.call(source))

    def test_reversed_addition_preserves_order(self) -> None:
        source = self.source(
            "    x = 'tail'\n    alias = x\n    x = 'head' + x\n    return alias, x\n"
        )
        self.assertEqual(self.call(self.rewrite(source)), ("tail", "headtail"))

    def test_subtraction_is_not_replaced_by_addition(self) -> None:
        source = self.source(
            "    y = value - 1\n    z = y * 2\n    q = z + 3\n    return q\n", "value"
        )
        self.assertEqual(self.call(self.rewrite(source), DistinctOperators()), 201)

    def test_shadowed_builtin_parameter_is_preserved(self) -> None:
        source = self.source(
            "    x = len(data)\n    y = x + 2\n    z = y * 3\n    return z\n", "len, data"
        )
        rewritten = self.rewrite(source)
        self.assertEqual(self.call(source, lambda _: 100, []), 306)
        self.assertEqual(self.call(rewritten, lambda _: 100, []), 306)

    def test_module_helpers_called_from_classes_are_not_mangled(self) -> None:
        source = (Path(__file__).parents[1] / "test_examples" / "example2_classes.py").read_text(
            encoding="utf-8"
        )
        rewritten = self.rewrite(source)
        for candidate in (source, rewritten):
            namespace: dict[str, object] = {}
            with contextlib.redirect_stdout(io.StringIO()):
                exec(candidate, namespace)
                exec(
                    "observed = []\n"
                    "for cls in (EmailProcessor, SMSProcessor, PushNotificationProcessor):\n"
                    "    observed.append(cls('a@b.c').process())\n"
                    "    for value in ('', 'invalid', 'a@b'):\n"
                    "        try:\n"
                    "            cls(value).process()\n"
                    "        except ValueError as error:\n"
                    "            observed.append(str(error))\n",
                    namespace,
                )
            self.assertEqual(
                namespace["observed"],
                [True, "Email is required", "Invalid email format", "Email too short"] * 3,
            )

    def test_inherited_helpers_avoid_mangling_and_existing_names(self) -> None:
        source = (
            "class Base:\n    pass\n"
            "class First(Base):\n"
            "    def _extracted_func_7(self):\n        return -1\n"
            "    def run(self, x):\n"
            "        a = x + 1\n        b = a * 2\n        c = b + 3\n        return c\n"
            "class Second(Base):\n"
            "    def run(self, x):\n"
            "        a = x + 1\n        b = a * 2\n        c = b + 3\n        return c\n"
        )
        rewritten = self.rewrite(source)
        self.assertIn("def _extracted_func_8", rewritten)
        namespace: dict[str, object] = {}
        exec(rewritten, namespace)
        exec(
            "observed = (First().run(2), Second().run(2), First()._extracted_func_7())",
            namespace,
        )
        self.assertEqual(namespace["observed"], (9, 9, -1))

    def test_cli_discovers_both_generated_helper_spellings(self) -> None:
        path = self.root / "helpers.py"
        path.write_text(
            "def __extracted_func_2():\n    pass\n"
            "class Base:\n    def _extracted_func_3(self):\n        pass\n",
            encoding="utf-8",
        )
        helpers = _find_extracted_helpers(self.root, None, None)
        self.assertEqual(
            {name for _, name, _, _ in helpers}, {"__extracted_func_2", "_extracted_func_3"}
        )

    def test_nested_extraction_preserves_bindings_used_later_in_loop(self) -> None:
        cases: tuple[tuple[str, tuple[str, str], tuple[object, ...]], ...] = (
            (
                "nested_structures.py",
                ("build_complex_structure_v1", "build_complex_structure_v2"),
                ([{"category": "x", "value": 3}, {"category": "x", "value": 5}], {}),
            ),
            (
                "real_world_patterns.py",
                ("aggregate_metrics_a", "aggregate_metrics_b"),
                ([{"timestamp": 12, "value": 3}, {"timestamp": 13, "value": 5}], 10, None),
            ),
        )
        for filename, names, arguments in cases:
            with self.subTest(filename=filename):
                fixture = Path(__file__).parents[1] / "test_examples" / filename
                module = ast.parse(fixture.read_text(encoding="utf-8"))
                functions: list[ast.stmt] = [
                    node
                    for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name in names
                ]
                source = ast.unparse(ast.Module(body=functions, type_ignores=[]))
                original: dict[str, object] = {}
                exec(source, original)
                expected = {
                    name: cast(Callable[..., object], original[name])(*arguments) for name in names
                }
                path = self.root / filename
                path.write_text(source, encoding="utf-8")
                engine = UnificationRefactorEngine(min_lines=3)
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    proposals = engine.analyze_files([str(path)], progress="none")
                    outputs = [
                        engine.apply_refactoring(str(path), proposal) for proposal in proposals
                    ]
                    final, _, _ = engine.refactor_to_fixed_point(str(path), max_iterations=10)
                for output in [*outputs, final]:
                    rewritten: dict[str, object] = {}
                    exec(output, rewritten)
                    for name in names:
                        self.assertEqual(
                            cast(Callable[..., object], rewritten[name])(*arguments), expected[name]
                        )

    def test_nested_binding_guard_covers_loop_carried_reads(self) -> None:
        module = ast.parse(
            "def example(items):\n"
            "    total = 0\n"
            "    for item in items:\n"
            "        print(total)\n"
            "        total = item * 2\n"
        )
        function = cast(ast.FunctionDef, module.body[0])
        loop = cast(ast.For, function.body[1])
        self.assertTrue(nested_bindings_escape(function, loop.body[1:]))

    def test_nested_binding_guard_allows_self_contained_local_calculation(self) -> None:
        module = ast.parse(
            "def example(items, output):\n"
            "    for item in items:\n"
            "        value = item * 2\n"
            "        adjusted = value + 1\n"
            "        output.append(adjusted)\n"
        )
        function = cast(ast.FunctionDef, module.body[0])
        loop = cast(ast.For, function.body[0])
        self.assertFalse(nested_bindings_escape(function, loop.body))

    def test_nested_import_and_pattern_bindings_remain_visible(self) -> None:
        cases = [
            ("import math as m", "m.sqrt(value)", 4, 2.0),
            ("from math import sqrt as root", "root(value)", 4, 2.0),
            (
                "import xml.etree.ElementTree",
                "xml.etree.ElementTree.fromstring(value).tag",
                "<root/>",
                "root",
            ),
        ]
        # Pattern grammar is supported starting with Python 3.10. Import
        # binding regressions run on every supported interpreter.
        if hasattr(ast, "Match"):
            cases.extend(
                [
                    (
                        "match value:\n            case {'n': captured, **rest}:\n                pass",
                        "(captured, rest)",
                        {"n": 2, "extra": 3},
                        (2, {"extra": 3}),
                    ),
                    (
                        "match value:\n            case [captured, *tail]:\n                pass",
                        "(captured, tail)",
                        [2, 3, 4],
                        (2, [3, 4]),
                    ),
                ]
            )
        for binding, expression, value, expected in cases:
            with self.subTest(binding=binding):
                shared = f"        {binding}\n        print('a')\n        print('b')\n        print('c')\n"
                source = (
                    "def first(flag, value):\n    if flag:\n"
                    + shared
                    + f"        return {expression}\n"
                    + "def second(items):\n    for value in items:\n"
                    + shared
                    + f"        yield {expression}\n"
                )
                path = self.root / "bindings.py"
                path.write_text(source, encoding="utf-8")
                engine = UnificationRefactorEngine(min_lines=3)
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    proposals = engine.analyze_files([str(path)], progress="none")
                    outputs = [source] + [
                        engine.apply_refactoring(str(path), proposal) for proposal in proposals
                    ]
                    for output in outputs:
                        namespace: dict[str, object] = {}
                        exec(output, namespace)
                        self.assertEqual(
                            cast(Callable[..., object], namespace["first"])(True, value), expected
                        )
                        iterator = cast(Callable[..., Iterator[object]], namespace["second"])(
                            [value]
                        )
                        self.assertEqual(list(iterator), [expected])

    def test_clustering_requires_the_existing_helper_template(self) -> None:
        attributes = (
            "    result = obj.value\n"
            "    result += obj.data[0]\n"
            "    result += obj.get_value()\n"
            "    return result\n"
        )
        membership = (
            "    result = 1 if x in data else 0\n"
            "    result += 1 if x not in data else 0\n"
            "    result += 1 if 'key' in {'key': x} else 0\n"
            "    return result\n"
        )
        source = "".join(
            f"def attributes_{suffix}(obj):\n{attributes}" for suffix in ("a", "b", "c")
        ) + "".join(f"def membership_{suffix}(x, data):\n{membership}" for suffix in ("a", "b"))
        path = self.root / "clustering.py"
        path.write_text(source, encoding="utf-8")
        # Clustering onto a shared helper is under test; with reuse the
        # attribute functions would simply call ``attributes_a``.
        engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
        with contextlib.redirect_stdout(io.StringIO()):
            proposals = engine.analyze_files([str(path)], progress="none")
        proposal = next(
            proposal
            for proposal in proposals
            if "attributes_a and attributes_b" in proposal.description
        )
        # Matching third occurrences still share the helper; generalized
        # membership blocks must not call an attribute-access helper.
        self.assertEqual(len(proposal.replacements), 3)
        rewritten = engine.apply_refactoring(str(path), proposal)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            final, count, _ = engine.refactor_to_fixed_point(str(path), max_iterations=10)
        self.assertGreater(count, 0)
        for candidate in (rewritten, final):
            namespace: dict[str, object] = {}
            exec(candidate, namespace)
            for suffix in ("a", "b", "c"):
                attribute_call = cast(Callable[[object], object], namespace[f"attributes_{suffix}"])
                value = SimpleNamespace(value=3, data=[3], get_value=lambda: 3)
                self.assertEqual(attribute_call(value), 9)
            for suffix in ("a", "b"):
                membership_call = cast(
                    Callable[[object, object], object], namespace[f"membership_{suffix}"]
                )
                for container in ({}, {0: 1}, [0], []):
                    self.assertEqual(membership_call(0, container), 2)

    def test_generator_extraction_is_rejected(self) -> None:
        for expression in ("yield 1", "yield from (1, 2)"):
            with self.subTest(expression=expression):
                self.assert_rejected(self.source(f"    {expression}\n" * 4))

    def test_frame_introspection_extraction_is_rejected(self) -> None:
        for expression in ("locals()", "vars()", "eval('secret')", "globals()"):
            with self.subTest(expression=expression):
                self.assert_rejected(
                    self.source(
                        f"    count = 1\n    result = {expression}\n    count = 2\n    return result\n",
                        "secret",
                    )
                )

    def test_cross_file_helper_placed_in_the_module_that_avoids_a_cycle(self) -> None:
        # ``a`` imports ``b``, so hosting the shared helper in ``a`` and importing
        # it from ``b`` would close a cycle. There is no pre-existing a<->b cycle,
        # so the helper can live safely in ``b`` (which ``a`` already imports).
        # The engine must pick that safe home rather than decline the extraction.
        body = "    y = x + 1\n    z = y * 2\n    q = z + 3\n    return q\n"
        a = self.root / "a.py"
        b = self.root / "b.py"
        a.write_text("import b\n\ndef first(x):\n" + body, encoding="utf-8")
        b.write_text("def second(x):\n" + body, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            proposals = UnificationRefactorEngine(cross_module_helpers=True).analyze_files(
                [str(a), str(b)], progress="none"
            )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(Path(proposals[0].file_path).name, "b.py")

    def test_transitive_cycle_through_module_without_functions(self) -> None:
        a = self.root / "a.py"
        b = self.root / "b.py"
        bridge = self.root / "bridge.py"
        a.write_text("import bridge\n", encoding="utf-8")
        bridge.write_text("import b\n", encoding="utf-8")
        b.write_text("", encoding="utf-8")
        self.assertTrue(would_create_import_cycle(str(a), {str(a), str(b)}, ImportGraphCache()))

    def test_relative_import_cycle(self) -> None:
        package = self.root / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        a, b = package / "a.py", package / "b.py"
        a.write_text("from . import b\n", encoding="utf-8")
        b.write_text("", encoding="utf-8")
        self.assertTrue(would_create_import_cycle(str(a), {str(a), str(b)}, ImportGraphCache()))

    def test_independent_modules_do_not_create_cycle(self) -> None:
        a, b = self.root / "a.py", self.root / "b.py"
        a.write_text("import math\n", encoding="utf-8")
        b.write_text("", encoding="utf-8")
        self.assertFalse(would_create_import_cycle(str(a), {str(a), str(b)}, ImportGraphCache()))

    def test_absolute_import_no_cycle_in_relocated_flat_layout(self) -> None:
        # An import of a package this tree does not hold names none of its
        # files, however its last component is spelled: no cycle is invented.
        a, b = self.root / "a.py", self.root / "b.py"
        a.write_text(
            "from app.other import seed\n\ndef first(x):\n    return x\n", encoding="utf-8"
        )
        b.write_text("seed = 1\n", encoding="utf-8")
        self.assertFalse(would_create_import_cycle(str(a), {str(a), str(b)}, ImportGraphCache()))
