"""
Detect orphaned variable references after code extraction.

An orphaned variable is one that is bound (assigned) in the extracted code
but referenced in code that remains after the extraction point.
"""

import ast
from typing import List, Set, Tuple


def get_bound_variables(nodes: List[ast.AST]) -> Set[str]:
    """
    Get all variables bound (assigned) in a block of code.

    This includes:
    - Assignment targets (x = ...)
    - For loop targets (for x in ...)
    - Function/class definitions
    - But NOT comprehension variables (they're local to the comprehension)
    """
    class BindingCollector(ast.NodeVisitor):
        def __init__(self):
            self.bindings = set()
            self.in_comprehension = False

        def visit_Assign(self, node):
            for target in node.targets:
                self._collect_names(target)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if node.target:
                self._collect_names(node.target)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            self._collect_names(node.target)
            self.generic_visit(node)

        def visit_For(self, node):
            self._collect_names(node.target)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            self.bindings.add(node.name)
            # Don't visit inside nested functions

        def visit_AsyncFunctionDef(self, node):
            self.bindings.add(node.name)
            # Don't visit inside nested functions

        def visit_ClassDef(self, node):
            self.bindings.add(node.name)
            # Don't visit inside nested classes

        def visit_ListComp(self, node):
            # Comprehension variables are local, don't collect them
            pass

        def visit_SetComp(self, node):
            pass

        def visit_DictComp(self, node):
            pass

        def visit_GeneratorExp(self, node):
            pass

        def _collect_names(self, node):
            """Collect all name nodes from a target."""
            if isinstance(node, ast.Name):
                self.bindings.add(node.id)
            elif isinstance(node, (ast.Tuple, ast.List)):
                for elt in node.elts:
                    self._collect_names(elt)
            elif isinstance(node, ast.Starred):
                self._collect_names(node.value)
            # Ignore subscripts and attributes (they don't create bindings)

    collector = BindingCollector()
    for node in nodes:
        collector.visit(node)
    return collector.bindings


def get_used_variables(nodes: List[ast.AST]) -> Set[str]:
    """
    Get all variables used (referenced) in a block of code.
    """
    class UsageCollector(ast.NodeVisitor):
        def __init__(self):
            self.uses = set()

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                self.uses.add(node.id)
            self.generic_visit(node)

    collector = UsageCollector()
    for node in nodes:
        collector.visit(node)
    return collector.uses


def has_orphaned_variables(
    function_body: List[ast.AST],
    extracted_block_range: Tuple[int, int]
) -> Tuple[bool, Set[str]]:
    """
    Check if extracting a block would create orphaned variable references.

    Args:
        function_body: All statements in the function
        extracted_block_range: (start_index, end_index) of block to extract
            These are 0-based indices into function_body

    Returns:
        (has_orphans, orphaned_vars) where:
        - has_orphans: True if there are orphaned variables
        - orphaned_vars: Set of variable names that would be orphaned
    """
    start_idx, end_idx = extracted_block_range

    # Get the extracted block and remaining code
    extracted_block = function_body[start_idx:end_idx + 1]
    remaining_code = function_body[end_idx + 1:]

    if not remaining_code:
        # Nothing after the extracted block, so no orphans possible
        return False, set()

    # Get variables bound in the extracted block
    bound_in_extracted = get_bound_variables(extracted_block)

    # Get variables used in the remaining code
    used_in_remaining = get_used_variables(remaining_code)

    # Get variables bound in the remaining code
    bound_in_remaining = get_bound_variables(remaining_code)

    # Orphaned variables are those that are:
    # 1. Bound in the extracted block
    # 2. Used in the remaining code
    # 3. NOT bound in the remaining code (before use)
    orphaned = bound_in_extracted & used_in_remaining - bound_in_remaining

    return len(orphaned) > 0, orphaned
