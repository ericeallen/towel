import ast
import textwrap
import unittest
from typing import Any

from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import TemporaryModuleTestCase


class TestAncestorInsertion(TemporaryModuleTestCase):
    def test_inserts_helper_into_common_ancestor_class(self):
        # Two sibling subclasses share identical validation blocks; expect insertion into BaseProcessor.
        code = textwrap.dedent("""
            class BaseProcessor:
                def __init__(self):
                    self._initialized = True

            class EmailProcessor(BaseProcessor):
                def validate(self, value):
                    if not value:
                        raise ValueError("required")
                    if len(value) < 3:
                        raise ValueError("too short")
                    if value.startswith("!"):
                        raise ValueError("bang not allowed")
                    return value.upper()

            class SMSProcessor(BaseProcessor):
                def validate(self, value):
                    if not value:
                        raise ValueError("required")
                    if len(value) < 3:
                        raise ValueError("too short")
                    if value.startswith("!"):
                        raise ValueError("bang not allowed")
                    return value.upper()
            """)
        path = self._write_temp(code)
        engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
        proposals = engine.analyze_file(path)
        self.assertTrue(
            proposals, "Expected at least one proposal for duplicated validation blocks"
        )
        ancestor_proposals = [p for p in proposals if p.insert_into_class == "BaseProcessor"]
        self.assertTrue(ancestor_proposals, "Expected insertion into the common ancestor")
        proposal = ancestor_proposals[0]
        modified = engine.apply_refactoring(path, proposal)
        tree = ast.parse(modified)
        base = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "BaseProcessor"
        )
        helpers = [
            node
            for node in base.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_extracted_func_")
        ]
        self.assertEqual(len(helpers), 1)
        self.assertIn(f"self.{helpers[0].name}(", modified)
        namespace: dict[str, Any] = {}
        exec(modified, namespace)
        for name in ("EmailProcessor", "SMSProcessor"):
            processor = namespace[name]()
            self.assertEqual(processor.validate("valid"), "VALID")
            for value, error in (
                ("", "required"),
                ("x", "too short"),
                ("!bad", "bang not allowed"),
            ):
                with self.assertRaisesRegex(ValueError, error):
                    processor.validate(value)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestAmbiguousCommonAncestor(TemporaryModuleTestCase):
    """Two classes can share several ancestors; the choice must not depend on their order.

    Runtime dispatch is indifferent, since every common ancestor is on both
    method resolution orders. The receiver's type is not: a helper lands in
    the chosen class, and a nearer ancestor exposes more of what the body may
    use, so a farther one can be refused where a nearer one type-checks.
    """

    BODY = (
        "        total = value + 1\n"
        "        doubled = total * 2\n"
        "        answer = doubled - 3\n"
        "        return answer\n"
    )

    def _placed_in(self, order: str) -> list[Any]:
        classes = {
            "X": "class X(Mid):\n    def go(self, value: int) -> int:\n" + self.BODY,
            "Y": "class Y(Root, Mid):\n    def go(self, value: int) -> int:\n" + self.BODY,
        }
        code = "class Root:\n    pass\n\n\nclass Mid(Root):\n    pass\n\n\n"
        code += "\n\n".join(classes[name] for name in order)
        path = self._write_temp(code)
        engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
        return [p.insert_into_class for p in engine.analyze_file(path)]

    def test_the_same_classes_give_the_same_home_in_either_order(self):
        self.assertEqual(self._placed_in("XY"), self._placed_in("YX"))

    def test_the_nearest_shared_ancestor_is_preferred(self):
        # Root is an ancestor of both too, but Mid is nearer to both.
        self.assertEqual(self._placed_in("XY"), ["Mid"])
