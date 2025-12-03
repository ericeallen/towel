# Copyright 2025 Eric Allen
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

"""
Utilities for AST visitor classes to ensure complete coverage.
"""

import ast
import os


from typing import Any, Callable


def make_defensive_generic_visit(visitor_class_name: str) -> Callable[[Any, ast.AST], Any]:
    """
    Create a defensive generic_visit method that catches unhandled AST nodes.

    In development mode (when DEBUG_AST_COVERAGE=1), this raises an error
    when an AST node type is encountered that doesn't have a specific visitor method.
    This helps catch missing visitor methods during testing.

    In production mode, it delegates to the parent's generic_visit.

    Args:
        visitor_class_name: Name of the visitor class (for error messages)

    Returns:
        A generic_visit method suitable for use in an AST visitor class

    Example:
        class MyVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit('MyVisitor')

            def visit_Name(self, node):
                # ... handle Name nodes
                return node
    """

    def generic_visit(self: Any, node: ast.AST) -> Any:
        """
        Fallback for unhandled nodes. In DEBUG mode, raises an error.
        In production, delegates to parent.
        """
        # Check if we're in debug mode
        if os.getenv("DEBUG_AST_COVERAGE"):
            # Get the list of visitor methods this class has
            visitor_methods = {
                name[6:]
                for name in dir(self)
                if name.startswith("visit_") and callable(getattr(self, name))
            }

            node_type = node.__class__.__name__

            # Only raise if this is a "real" AST node (not auxiliary types)
            # and we don't have a visitor for it
            if node_type not in visitor_methods and hasattr(ast, node_type):
                raise NotImplementedError(
                    f"Missing visitor method for {node_type} in {visitor_class_name}. "
                    f"Please implement visit_{node_type}() or verify that "
                    f"generic traversal is appropriate for this node type."
                )

        # In production or for nodes we intentionally don't handle,
        # delegate to the parent's generic_visit
        if isinstance(self, ast.NodeTransformer):
            return ast.NodeTransformer.generic_visit(self, node)
        else:
            return ast.NodeVisitor.generic_visit(self, node)

    return generic_visit
