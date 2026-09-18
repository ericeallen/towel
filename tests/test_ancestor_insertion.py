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
