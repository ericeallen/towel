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
AST Pretty Printer

A utility for displaying Python AST structures in a human-readable format.
"""

import ast
from typing import Any, Optional, List


class ASTPrettyPrinter:
    """Pretty-print Python AST nodes in a readable tree format."""

    def __init__(self, indent_size: int = 2, show_line_numbers: bool = False):
        self.indent_size = indent_size
        self.show_line_numbers = show_line_numbers

    def format(self, node: ast.AST, indent: int = 0) -> str:
        """Format an AST node as a pretty-printed string."""
        lines: List[str] = []
        self._format_node(node, indent, lines)
        return "\n".join(lines)

    def _format_node(self, node: Any, indent: int, lines: List[str]) -> None:
        """Recursively format an AST node."""
        prefix = " " * indent

        if isinstance(node, ast.AST):
            # Format the node type
            node_name = node.__class__.__name__

            # Add line number if available and requested
            if self.show_line_numbers and hasattr(node, "lineno"):
                node_name = f"{node_name}@L{node.lineno}"

            lines.append(f"{prefix}{node_name}(")

            # Get all fields for this node
            for field_name, field_value in ast.iter_fields(node):
                field_prefix = " " * (indent + self.indent_size)

                if field_value is None:
                    continue
                elif isinstance(field_value, list):
                    if not field_value:
                        continue
                    lines.append(f"{field_prefix}{field_name}=[")
                    for item in field_value:
                        self._format_node(item, indent + self.indent_size * 2, lines)
                    lines.append(f"{field_prefix}]")
                elif isinstance(field_value, ast.AST):
                    lines.append(f"{field_prefix}{field_name}=")
                    self._format_node(field_value, indent + self.indent_size * 2, lines)
                else:
                    # Primitive value (string, int, etc.)
                    lines.append(f"{field_prefix}{field_name}={self._format_value(field_value)}")

            lines.append(f"{prefix})")

        elif isinstance(node, list):
            lines.append(f"{prefix}[")
            for item in node:
                self._format_node(item, indent + self.indent_size, lines)
            lines.append(f"{prefix}]")

        else:
            # Primitive value
            lines.append(f"{prefix}{self._format_value(node)}")

    def _format_value(self, value: Any) -> str:
        """Format a primitive value for display."""
        if isinstance(value, str):
            return repr(value)
        else:
            return str(value)

    def print(self, node: ast.AST, title: Optional[str] = None) -> None:
        """Print an AST node with an optional title."""
        if title:
            print("=" * 80)
            print(title)
            print("=" * 80)
        print(self.format(node))
        print()


def print_ast(
    node: ast.AST,
    title: Optional[str] = None,
    indent_size: int = 2,
    show_line_numbers: bool = False,
) -> None:
    """
    Convenience function to pretty-print an AST node.

    Args:
        node: The AST node to print
        title: Optional title to display before the AST
        indent_size: Number of spaces per indentation level
        show_line_numbers: Whether to show line numbers in the output
    """
    printer = ASTPrettyPrinter(indent_size=indent_size, show_line_numbers=show_line_numbers)
    printer.print(node, title=title)


def compare_asts(
    node1: ast.AST, node2: ast.AST, title1: str = "AST 1", title2: str = "AST 2"
) -> None:
    """
    Print two ASTs side by side for comparison.

    Args:
        node1: First AST node
        node2: Second AST node
        title1: Title for first AST
        title2: Title for second AST
    """
    print_ast(node1, title=title1)
    print_ast(node2, title=title2)
