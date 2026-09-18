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

"""Where a generated helper and its import go in a module.

A module-level helper is placed before the first definition, after the
docstring and imports, or after the last definition its annotations name
when nothing before that point runs code at import; a method helper goes at
the end of its class body; a function-local helper goes before the first
executable statement of its function. The positions are found from the
parsed module, never from text, so imports inside strings and comments are
never mistaken for imports. Rendered lines are re-indented to the unit the
file uses.
"""

from __future__ import annotations

import ast

from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple, Union, cast
from .pipeline import parse_cached
from .visitors import ClassLocator, FuncLocator, body_without_docstring

from .engine_state import EngineState
from .models import FunctionNode
from ..source_text import read_source


def reindent(line: str, prefix: str) -> str:
    """Prefix an ``ast.unparse`` line, converting its 4-space levels to the file's unit."""
    stripped = line.lstrip(" ")
    levels = (len(line) - len(stripped)) // 4
    unit = "\t" if "\t" in prefix else "    "
    return prefix + unit * levels + stripped


def relative_import_module(from_path: Path, to_path: Path) -> Optional[str]:
    """The relative-import module for reaching ``from_path`` from ``to_path``.

    Ascends from the importing file's own directory until it contains the helper
    file, using one leading dot for that package plus one more per level climbed:
    ``.helpers`` for a sibling module, ``.sub.helpers`` for one in a subpackage,
    ``..helpers`` for one a level up. Returns ``None`` when the two files share no
    directory tree, so a relative import cannot reach across.
    """
    helper = from_path.resolve()
    package_dir = to_path.resolve().parent
    dots = 1
    while True:
        try:
            relative = helper.relative_to(package_dir)
            break
        except ValueError:
            parent = package_dir.parent
            if parent == package_dir:
                return None
            package_dir = parent
            dots += 1
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return "." * dots + ".".join(parts)


class InsertionPoints(EngineState):
    """InsertionPoints methods of the engine; see the module docstring."""

    _DEFINITION_LIKE = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
    )

    @staticmethod
    def _block_line_span(block: Sequence[ast.stmt]) -> Optional[Tuple[int, int]]:
        """Return the (start_line, end_line) span for a contiguous block of statements."""

        if not block:
            return None

        start_node = block[0]
        end_node = block[-1]

        start_line = getattr(start_node, "lineno", None)
        end_line = getattr(end_node, "end_lineno", None) or getattr(end_node, "lineno", None)
        if start_line is None or end_line is None:
            return None

        return int(start_line), int(end_line)

    def _source_lines(self, file_path: str) -> Sequence[str]:
        """The file's lines, re-read only when its size or modification time changed.

        The fixed-point loop applies one proposal at a time, so a file touched by
        several proposals was read once per proposal; the stat check keeps the
        memo exact across the rewrites in between.
        """
        path = Path(file_path)
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._source_lines_cache.get(file_path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        lines = tuple(read_source(file_path).splitlines(keepends=True))
        self._source_lines_cache[file_path] = (signature, lines)
        if len(self._source_lines_cache) > 64:
            self._source_lines_cache.pop(next(iter(self._source_lines_cache)))
        return lines

    def _find_import_position(self, lines: List[str]) -> int:
        """Return the 0-based line index at which to insert a new import.

        The position follows the module docstring and any leading imports,
        determined from the parsed module so that text inside comments or
        docstrings is never mistaken for an import.
        """
        body = parse_cached("".join(lines)).body
        position = 0
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            position = body[0].end_lineno or body[0].lineno
            body = body[1:]
        for statement in body:
            if not isinstance(statement, (ast.Import, ast.ImportFrom)):
                break
            position = statement.end_lineno or statement.lineno
        return position

    def _get_indent(self, line: str) -> str:
        """Get the indentation of a line."""
        return line[: len(line) - len(line.lstrip())]

    @classmethod
    def _is_definition_like(cls, statement: ast.stmt) -> bool:
        """Whether a module-level statement runs no code of the module's own at import.

        Definitions, imports, assignments and docstrings; an ``if`` or ``try``
        whose bodies are all such statements (``TYPE_CHECKING`` guards,
        optional imports). Anything else may call into the module, so a helper
        must be defined before it.
        """
        if isinstance(statement, cls._DEFINITION_LIKE):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            return True
        if isinstance(statement, ast.If):
            return all(cls._is_definition_like(s) for s in statement.body + statement.orelse)
        if isinstance(statement, ast.Try):
            nested = statement.body + statement.orelse + statement.finalbody
            nested += [s for handler in statement.handlers for s in handler.body]
            return all(cls._is_definition_like(s) for s in nested)
        return False

    @classmethod
    def placeable_after(cls, source: str) -> Set[str]:
        """Names of module-level definitions a helper can safely be placed after.

        A helper placed after the definitions its annotations name can spell
        them bare. It may move past a definition only if everything from the
        top of the module to that definition is definition-like, so no code
        that could call the helper runs before it is defined.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        names: Set[str] = set()
        for statement in tree.body:
            if not cls._is_definition_like(statement):
                break
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(statement.name)
            elif isinstance(statement, ast.Assign):
                names.update(t.id for t in statement.targets if isinstance(t, ast.Name))
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                names.add(statement.target.id)
        return names

    def _find_insert_position(
        self, lines: List[str], after_names: Optional[Set[str]] = None
    ) -> int:
        """Place a helper before the first definition, after any leading imports.

        Helpers have no evaluated defaults. When ``after_names`` is given (the
        names a helper's annotations refer to), the helper goes after the last
        module-level definition of one of them instead, so those annotations
        can be written bare; the caller guarantees through ``placeable_after``
        that nothing before that point runs code at import.
        """
        tree = parse_cached("".join(lines))
        after = 0
        after_names = after_names or set()
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if statement.name in after_names:
                    after = max(after, statement.end_lineno or statement.lineno)
            elif isinstance(statement, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id in after_names for t in statement.targets):
                    after = max(after, statement.end_lineno or statement.lineno)
            elif isinstance(statement, ast.AnnAssign):
                if isinstance(statement.target, ast.Name) and statement.target.id in after_names:
                    after = max(after, statement.end_lineno or statement.lineno)
        if after:
            return after
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return min([statement.lineno] + [d.lineno for d in statement.decorator_list]) - 1
        return len(lines)

    def _find_class_insert_position(
        self, source: str, class_name: str
    ) -> Optional[Tuple[int, str]]:
        """
        Find insertion position (0-based line index) at end of class body and class indentation.

        Returns (insert_line_index, class_indent_str) or None.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        locator = ClassLocator(source, class_name)
        locator.visit(tree)
        return locator.result

    def _find_function_insert_position_before_body_statements(
        self, source: str, function_name: str
    ) -> Optional[Tuple[int, str]]:
        """
        Find an insertion position (0-based line index) inside the given function BEFORE
        executable body statements (i.e., after any docstring and after any leading
        nested defs), along with the function's indentation.

        This ensures the inserted helper is bound before returns/calls are executed.
        Returns (insert_line_index, function_indent_str) or None if function not found.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        locator = FuncLocator(source, function_name)
        locator.visit(tree)
        return locator.result

    def _get_block_indices(
        self, function: FunctionNode, block_nodes: Sequence[ast.AST]
    ) -> Optional[Tuple[int, int]]:
        """
        Find the indices of a block within a function body.

        Args:
            function: Function definition
            block_nodes: Block to find

        Returns:
            (start_index, end_index) or None if not found
        """
        if not block_nodes:
            return None

        # Get function body (skip docstring)
        body = body_without_docstring(function.body)

        # Match by line numbers
        first_node = cast(Union[ast.stmt, ast.expr], block_nodes[0])
        last_node = cast(Union[ast.stmt, ast.expr], block_nodes[-1])
        block_start_line = first_node.lineno
        block_end_line = (
            last_node.end_lineno
            if hasattr(last_node, "end_lineno") and last_node.end_lineno is not None
            else last_node.lineno
        )

        # Find matching range in body
        for i, stmt in enumerate(body):
            stmt_start = stmt.lineno
            stmt_end = stmt.end_lineno if hasattr(stmt, "end_lineno") else stmt.lineno

            if stmt_start == block_start_line:
                # Found start, now find end
                for j in range(i, len(body)):
                    stmt_end = (
                        body[j].end_lineno if hasattr(body[j], "end_lineno") else body[j].lineno
                    )
                    if stmt_end == block_end_line:
                        return (i, j)

        return None
