"""
Main refactoring engine using unification.

This orchestrates the entire refactoring process:
1. Parse files into ASTs
2. Find pairs of code blocks in top-level functions
3. Attempt unification to find parameterizable differences
4. Extract functions hygienically if unification succeeds
5. Generate replacement calls
"""

import ast
from typing import List, Tuple, Dict, Set, Optional
from pathlib import Path
from dataclasses import dataclass, field

from .scope_analyzer import ScopeAnalyzer, Scope
from .unifier import Unifier
from .extractor import HygienicExtractor, is_value_producing
from .orphan_detector import has_orphaned_variables
from .assignment_analyzer import analyze_assignments, has_reassignments_without_bindings


@dataclass
class CodeBlockPair:
    """Represents a pair of potentially duplicate code blocks."""

    file_path: str
    function1_name: str
    function2_name: str
    block1_range: Tuple[int, int]  # (start_line, end_line)
    block2_range: Tuple[int, int]
    block1_nodes: List[ast.AST]
    block2_nodes: List[ast.AST]
    file_path2: Optional[str] = None  # For cross-file pairs
    scope_analyzer1: Optional["ScopeAnalyzer"] = None
    scope_analyzer2: Optional["ScopeAnalyzer"] = None
    root_scope1: Optional["Scope"] = None
    root_scope2: Optional["Scope"] = None
    source1: Optional[str] = None
    source2: Optional[str] = None


@dataclass
class RefactoringProposal:
    """Proposed refactoring."""

    file_path: str
    extracted_function: ast.FunctionDef
    # (line_range, replacement_node) or (line_range, replacement_node, file_path)
    replacements: List[Tuple]
    description: str
    parameters_count: int
    return_variables: List[str] = field(
        default_factory=list
    )  # Variables that extracted function returns


class UnificationRefactorEngine:
    """
    Main engine for unification-based refactoring.

    This finds and extracts duplicate code using unification.
    """

    def __init__(
        self,
        max_parameters: int = 5,
        min_lines: int = 4,
        parameterize_constants: bool = True,
        allow_global_reads_without_assignment: bool = False,
    ):
        """
        Initialize the refactoring engine.

        Args:
            max_parameters: Maximum parameters for extracted functions
            min_lines: Minimum lines for a code block
            parameterize_constants: Whether to parameterize differing constants
        """
        self.max_parameters = max_parameters
        self.min_lines = min_lines
        self.parameterize_constants = parameterize_constants
        self.unifier = Unifier(
            max_parameters=max_parameters, parameterize_constants=parameterize_constants
        )
        self.extractor = HygienicExtractor()
        # When True, allows extraction even if a free variable is declared global/nonlocal
        # in the original function, provided the name itself is not assigned within the block.
        # This enables patterns like `log.append(x)` where `log` is global, without making it
        # a parameter or declaring it global in the extracted function.
        self.allow_global_reads_without_assignment = allow_global_reads_without_assignment

    def analyze_file(self, file_path: str) -> List[RefactoringProposal]:
        """
        Analyze a Python file and find refactoring opportunities.

        Args:
            file_path: Path to Python file

        Returns:
            List of refactoring proposals
        """
        return self.analyze_files([file_path])

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        *,
        verbose: bool = False,
        progress: str = "auto",
    ) -> List[RefactoringProposal]:
        """
        Analyze all Python files in a directory and find refactoring opportunities.

        Args:
            directory: Path to directory
            recursive: Whether to search subdirectories (default: True)

        Returns:
            List of refactoring proposals
        """
        # Find all Python files
        python_files = self._find_python_files(directory, recursive)

        if not python_files:
            return []

        if verbose:
            print(f"Found {len(python_files)} Python files in {directory}")

        # Analyze all files together
        return self.analyze_files(python_files, verbose=verbose, progress=progress)

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """
        Find all Python files in a directory.

        Args:
            directory: Directory to search
            recursive: Whether to search subdirectories

        Returns:
            List of Python file paths
        """
        python_files = []
        directory_path = Path(directory)

        if not directory_path.exists():
            return []

        if recursive:
            # Recursively find all .py files
            for py_file in directory_path.rglob("*.py"):
                # Skip common directories to ignore
                if any(
                    part.startswith(".") or part in ["__pycache__", "venv", "env", "node_modules"]
                    for part in py_file.parts
                ):
                    continue
                python_files.append(str(py_file))
        else:
            # Only find .py files in this directory
            for py_file in directory_path.glob("*.py"):
                python_files.append(str(py_file))

        return sorted(python_files)

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        verbose: bool = False,
        progress: str = "auto",
    ) -> List[RefactoringProposal]:
        """
        Analyze multiple Python files and find refactoring opportunities.

        Args:
            file_paths: List of paths to Python files

        Returns:
            List of refactoring proposals
        """
        # Parse all files
        all_functions = []  # List of (file_path, function_node, source, scope_analyzer, root_scope)

        for file_path in file_paths:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()

            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            # Analyze scopes
            scope_analyzer = ScopeAnalyzer()
            root_scope = scope_analyzer.analyze(tree)

            # Extract top-level functions
            top_level_functions = [
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]

            for func in top_level_functions:
                all_functions.append((file_path, func, source, scope_analyzer, root_scope))

        if len(all_functions) < 2:
            return []

        if verbose:
            print(f"Parsed {len(all_functions)} top-level functions from {len(file_paths)} file(s)")

        # Find pairs of code blocks across all functions (including cross-file)
        block_pairs = self._find_block_pairs_multi_file(all_functions)

        if verbose:
            total_pairs = len(block_pairs)
            print(f"Evaluating {total_pairs} candidate block pair(s)...")

        # Process each pair - try unification
        proposals = []

        # Choose progress style
        use_tqdm = False
        tqdm_iter = None
        if verbose and progress in ("auto", "tqdm"):
            try:
                import importlib
                _tqdm_mod = importlib.import_module('tqdm.auto')
                _tqdm = getattr(_tqdm_mod, 'tqdm')
                tqdm_iter = _tqdm(
                    block_pairs,
                    total=len(block_pairs),
                    desc="Analyzing candidate pairs",
                    unit="pair",
                    leave=False,
                )
                use_tqdm = True
            except Exception:
                use_tqdm = False

        if use_tqdm and tqdm_iter is not None:
            for pair in tqdm_iter:
                proposal = self._try_refactor_pair_multi_file(pair, all_functions)
                if proposal:
                    proposals.append(proposal)
        else:
            # Fallback: inline single-line bar using carriage returns (no extra deps)
            use_inline_bar = verbose and progress in ("auto", "tqdm") and len(block_pairs) > 0
            last_pct = -1
            if use_inline_bar:
                # Initial line
                print("Analyzing candidate pairs:", end=" ", flush=True)
            for idx, pair in enumerate(block_pairs, 1):
                proposal = self._try_refactor_pair_multi_file(pair, all_functions)
                if proposal:
                    proposals.append(proposal)
                if use_inline_bar:
                    pct = int(100 * idx / len(block_pairs))
                    if pct != last_pct:
                        last_pct = pct
                        bar_len = 24
                        filled = (pct * bar_len) // 100
                        bar = "#" * filled + "-" * (bar_len - filled)
                        print(f"\rAnalyzing candidate pairs: [{bar}] {pct}%", end="", flush=True)
            if use_inline_bar:
                print()  # newline after bar

        # Prefer larger extractions and de-duplicate overlaps greedily
        proposals = filter_overlapping_proposals(proposals)
        if verbose:
            print(f"Found {len(proposals)} non-overlapping proposal(s)")
        return proposals

    def _extract_code_blocks(
        self, function: ast.FunctionDef
    ) -> List[Tuple[Tuple[int, int], List[ast.AST]]]:
        """
        Extract all contiguous code blocks from a function body, including nested bodies.

        Args:
            function: Function definition

        Returns:
            List of (line_range, statements) tuples
        """

        def extract_from_body(body: List[ast.stmt]) -> List[Tuple[Tuple[int, int], List[ast.AST]]]:
            # Extract all contiguous subsequences of minimum length from a given body
            results: List[Tuple[Tuple[int, int], List[ast.AST]]] = []

            # Extract all contiguous subsequences
            for length in range(len(body), 0, -1):
                for start in range(len(body) - length + 1):
                    block = body[start : start + length]

                    if not block:
                        continue

                    start_line = block[0].lineno
                    end_line = (
                        block[-1].end_lineno
                        if hasattr(block[-1], "end_lineno")
                        else block[-1].lineno
                    )
                    line_count = end_line - start_line + 1

                    if line_count >= self.min_lines:
                        results.append(((start_line, end_line), block))

            # Recurse into nested bodies for control-flow/container statements
            for stmt in body:
                # Skip nested function/class definitions to avoid crossing scopes
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue

                # Common body/orelse containers
                if hasattr(stmt, "body") and isinstance(getattr(stmt, "body"), list):
                    results.extend(extract_from_body(getattr(stmt, "body")))
                if hasattr(stmt, "orelse") and isinstance(getattr(stmt, "orelse"), list):
                    results.extend(extract_from_body(getattr(stmt, "orelse")))

                # With and AsyncWith already covered by .body
                # Try/Except/Finally blocks
                if isinstance(stmt, ast.Try):
                    if stmt.handlers:
                        for h in stmt.handlers:
                            if hasattr(h, "body") and isinstance(h.body, list):
                                results.extend(extract_from_body(h.body))
                    if hasattr(stmt, "finalbody") and isinstance(stmt.finalbody, list):
                        results.extend(extract_from_body(stmt.finalbody))

            return results

        # Prepare top-level body (skip docstring)
        body = function.body
        start_idx = 0
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            start_idx = 1
        body = body[start_idx:]

        return extract_from_body(body)

    def _has_code_after_block(self, function: ast.FunctionDef, block_end_line: int) -> bool:
        """
        Check if there's any executable code after block_end_line in the function.

        This is used to detect when extracting a block with returns would make
        subsequent code unreachable.

        Args:
            function: Function definition
            block_end_line: End line of the block

        Returns:
            True if there's code after the block
        """
        body = function.body

        # Skip docstring if present
        start_idx = 0
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            start_idx = 1

        body = body[start_idx:]

        # Check if any statement starts after block_end_line
        for stmt in body:
            if stmt.lineno > block_end_line:
                return True
        return False

    def _has_returns_in_loops(self, block: List[ast.AST]) -> bool:
        """
        Check if a block contains return statements inside loops.

        Returns in loops are conditional on the loop executing, so if the loop
        doesn't execute (e.g., empty iteration), control continues after the loop.

        Args:
            block: List of AST statements

        Returns:
            True if there are returns inside loop statements
        """

        class LoopReturnFinder(ast.NodeVisitor):
            def __init__(self):
                self.has_loop_return = False
                self.in_loop = False

            def visit_For(self, node):
                # Enter loop context
                old_in_loop = self.in_loop
                self.in_loop = True
                self.generic_visit(node)
                self.in_loop = old_in_loop

            def visit_While(self, node):
                # Enter loop context
                old_in_loop = self.in_loop
                self.in_loop = True
                self.generic_visit(node)
                self.in_loop = old_in_loop

            def visit_Return(self, node):
                if self.in_loop:
                    self.has_loop_return = True

            def visit_FunctionDef(self, node):
                # Don't descend into nested functions
                pass

            def visit_AsyncFunctionDef(self, node):
                # Don't descend into nested async functions
                pass

        finder = LoopReturnFinder()
        for stmt in block:
            finder.visit(stmt)
        return finder.has_loop_return

    def _get_used_names(self, node: ast.AST) -> Set[str]:
        """
        Get all variable names that are used (read from) in an AST node.

        This collects all Name nodes with Load context.

        Args:
            node: AST node to analyze

        Returns:
            Set of variable names that are read in the node
        """
        used = set()

        class NameCollector(ast.NodeVisitor):
            def visit_Name(self, n):
                if isinstance(n.ctx, ast.Load):
                    used.add(n.id)
                self.generic_visit(n)

            def visit_FunctionDef(self, n):
                # Don't descend into nested functions
                pass

            def visit_AsyncFunctionDef(self, n):
                # Don't descend into nested async functions
                pass

        collector = NameCollector()
        collector.visit(node)
        return used

    def _find_block_pairs_multi_file(
        self, all_functions: List[Tuple[str, ast.FunctionDef, str, ScopeAnalyzer, Scope]]
    ) -> List[CodeBlockPair]:
        """
        Find all non-overlapping pairs of code blocks across multiple files.

        Args:
            all_functions: List of (file_path, function, source, scope_analyzer, root_scope)

        Returns:
            List of code block pairs
        """
        pairs = []

        # For each pair of functions (including across files)
        for i, (file1, func1, source1, analyzer1, scope1) in enumerate(all_functions):
            for file2, func2, source2, analyzer2, scope2 in all_functions[i + 1 :]:
                # Extract all code blocks from each function
                blocks1 = self._extract_code_blocks(func1)
                blocks2 = self._extract_code_blocks(func2)

                # Compare all pairs of blocks
                for block1_range, block1_nodes in blocks1:
                    for block2_range, block2_nodes in blocks2:
                        # Must have same number of statements for unification
                        if len(block1_nodes) != len(block2_nodes):
                            continue

                        # Check minimum size
                        start1, end1 = block1_range
                        start2, end2 = block2_range
                        if (end1 - start1 + 1) < self.min_lines or (
                            end2 - start2 + 1
                        ) < self.min_lines:
                            continue

                        # Create pair with all necessary context
                        pair = CodeBlockPair(
                            file_path=file1,
                            function1_name=func1.name,
                            function2_name=func2.name,
                            block1_range=block1_range,
                            block2_range=block2_range,
                            block1_nodes=block1_nodes,
                            block2_nodes=block2_nodes,
                            file_path2=file2,
                            scope_analyzer1=analyzer1,
                            scope_analyzer2=analyzer2,
                            root_scope1=scope1,
                            root_scope2=scope2,
                            source1=source1,
                            source2=source2,
                        )
                        pairs.append(pair)

        return pairs

    def _try_refactor_pair_multi_file(
        self,
        pair: CodeBlockPair,
        all_functions: List[Tuple[str, ast.FunctionDef, str, ScopeAnalyzer, Scope]],
    ) -> Optional[RefactoringProposal]:
        """
        Try to refactor a pair of code blocks using unification (cross-file support).

        Args:
            pair: Code block pair (may be cross-file)
            all_functions: All functions being analyzed

        Returns:
            Refactoring proposal or None
        """
        # DEBUG logging
        import os

        if os.getenv("DEBUG_VALIDATION"):
            print("\n=== _try_refactor_pair_multi_file called ===")
            print(f"Functions: {pair.function1_name} and {pair.function2_name}")
            print(f"Block1 range: {pair.block1_range}")
            print(f"Block2 range: {pair.block2_range}")

        # Use stored context from pair
        scope_analyzer = pair.scope_analyzer1
        root_scope = pair.root_scope1

        # Find the function definitions for context
        func1 = None
        func2 = None
        for file_path, func, _, _, _ in all_functions:
            if file_path == pair.file_path and func.name == pair.function1_name:
                func1 = func
            if (
                file_path == (pair.file_path2 or pair.file_path)
                and func.name == pair.function2_name
            ):
                func2 = func

        # DEBUG logging
        import os

        if os.getenv("DEBUG_VALIDATION"):
            print("\n=== Finding Functions ===")
            print(f"Looking for: {pair.function1_name} and {pair.function2_name}")
            print(f"Found func1: {func1 is not None}")
            print(f"Found func2: {func2 is not None}")

        # CRITICAL: Validate that blocks don't contain reassignments without initial bindings
        # Initialize return_variables tracking
        # This will be populated if we find variables that need to be returned
        return_variables_block1 = set()
        return_variables_block2 = set()

        # This prevents extracting code like "result = result + 10" when "result = x * 2"
        # is outside the block. Such extractions are fundamentally unsound.
        if func1 and func2:
            # Analyze assignments in both functions
            reassignments1 = analyze_assignments(func1)
            reassignments2 = analyze_assignments(func2)

            # Check if block1 contains reassignments without bindings
            has_unsafe1, problematic_vars1 = has_reassignments_without_bindings(
                func1, pair.block1_nodes, reassignments1
            )
            if has_unsafe1:
                # Cannot safely extract this block - it reassigns variables bound outside the block
                return None

            # Check if block2 contains reassignments without bindings
            has_unsafe2, problematic_vars2 = has_reassignments_without_bindings(
                func2, pair.block2_nodes, reassignments2
            )
            if has_unsafe2:
                # Cannot safely extract this block - it reassigns variables bound outside the block
                return None

            # CRITICAL: Validate that blocks don't bind variables used after the block
            # This prevents extracting partial lifetimes like:
            #   result = 1          # Initial binding (extracted)
            #   result += 2         # Reassignment (extracted)
            #   return result       # Use after block (NOT extracted) - ERROR!
            # The extracted function would create a LOCAL `result` that's never available outside
            # This applies to BOTH value-producing and non-value-producing blocks
            from .assignment_analyzer import _collect_bindings_and_reassignments

            # Check block1
            bound_in_block1 = set()
            reassigned_in_block1 = set()
            for node in pair.block1_nodes:
                _collect_bindings_and_reassignments(
                    node, reassignments1, bound_in_block1, reassigned_in_block1
                )

            # Find variables bound BEFORE the block starts
            bound_before_block1 = set()
            block_start_line = pair.block1_range[0]
            for stmt in func1.body:
                if hasattr(stmt, "lineno") and stmt.lineno < block_start_line:
                    # Collect bindings from statements before the block
                    stmt_bound = set()
                    stmt_reassigned = set()
                    _collect_bindings_and_reassignments(
                        stmt, reassignments1, stmt_bound, stmt_reassigned
                    )
                    bound_before_block1.update(stmt_bound)

            # Treat function parameters as bound before the block
            if isinstance(func1, ast.FunctionDef):
                param_names1 = set()
                for arg in func1.args.args:
                    param_names1.add(arg.arg)
                for arg in getattr(func1.args, "posonlyargs", []) or []:
                    param_names1.add(arg.arg)
                for arg in func1.args.kwonlyargs:
                    param_names1.add(arg.arg)
                if func1.args.vararg:
                    param_names1.add(func1.args.vararg.arg)
                if func1.args.kwarg:
                    param_names1.add(func1.args.kwarg.arg)
                bound_before_block1.update(param_names1)

            # Find variables bound AFTER the block ends
            bound_after_block1 = set()
            block_end_line = pair.block1_range[1]
            for stmt in func1.body:
                if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                    stmt_bound = set()
                    stmt_reassigned = set()
                    _collect_bindings_and_reassignments(
                        stmt, reassignments1, stmt_bound, stmt_reassigned
                    )
                    bound_after_block1.update(stmt_bound)

            # Variables that are bound in the block but DON'T exist before
            # These are the "newly introduced" variables
            initially_bound1 = bound_in_block1 - bound_before_block1

            # DEBUG logging
            import os

            if os.getenv("DEBUG_VALIDATION"):
                print("\n=== Block1 Validation Debug ===")
                print(f"Function: {pair.function1_name}")
                print(f"Block lines: {pair.block1_range}")
                print(f"Bound in block: {bound_in_block1}")
                print(f"Bound before block: {bound_before_block1}")
                print(f"Newly bound in block: {initially_bound1}")

            # Track variables that need to be returned from the extracted function
            # These are variables initially bound in the block but used after the block
            return_variables_block1 = set()

            # Check if any initially bound variables are used after the block
            if initially_bound1:
                # Find the position of the block in the function
                block_end_line = pair.block1_range[1]

                if os.getenv("DEBUG_VALIDATION"):
                    print(f"Block ends at line {block_end_line}")
                    print(f"Checking statements after line {block_end_line}:")

                # Check if any of these variables are used after the block
                for stmt in func1.body:
                    if hasattr(stmt, "lineno"):
                        if os.getenv("DEBUG_VALIDATION"):
                            print(f"  Statement at line {stmt.lineno}: {stmt.__class__.__name__}")

                        if stmt.lineno > block_end_line:
                            # Check if stmt uses any of the initially bound variables
                            uses = self._get_used_names(stmt)
                            if os.getenv("DEBUG_VALIDATION"):
                                print(f"    Uses: {uses}")

                            if uses & initially_bound1:
                                # Variable is bound in block and used after
                                # This is recoverable - we'll make the extracted function return these variables
                                return_variables_block1.update(uses & initially_bound1)
                                if os.getenv("DEBUG_VALIDATION"):
                                    print(
                                        f"    RETURN NEEDED: Variable(s) {uses & initially_bound1} will be returned from extracted function"
                                    )

                if os.getenv("DEBUG_VALIDATION") and return_variables_block1:
                    print(f"Block1 requires returning: {return_variables_block1}")

            # Same check for block2
            bound_in_block2 = set()
            reassigned_in_block2 = set()
            for node in pair.block2_nodes:
                _collect_bindings_and_reassignments(
                    node, reassignments2, bound_in_block2, reassigned_in_block2
                )

            # Find variables bound BEFORE block2 starts
            bound_before_block2 = set()
            block_start_line = pair.block2_range[0]
            for stmt in func2.body:
                if hasattr(stmt, "lineno") and stmt.lineno < block_start_line:
                    stmt_bound = set()
                    stmt_reassigned = set()
                    _collect_bindings_and_reassignments(
                        stmt, reassignments2, stmt_bound, stmt_reassigned
                    )
                    bound_before_block2.update(stmt_bound)

            # Treat function parameters as bound before the block
            if isinstance(func2, ast.FunctionDef):
                param_names2 = set()
                for arg in func2.args.args:
                    param_names2.add(arg.arg)
                for arg in getattr(func2.args, "posonlyargs", []) or []:
                    param_names2.add(arg.arg)
                for arg in func2.args.kwonlyargs:
                    param_names2.add(arg.arg)
                if func2.args.vararg:
                    param_names2.add(func2.args.vararg.arg)
                if func2.args.kwarg:
                    param_names2.add(func2.args.kwarg.arg)
                bound_before_block2.update(param_names2)

            # Find variables bound AFTER block2 ends
            bound_after_block2 = set()
            block_end_line = pair.block2_range[1]
            for stmt in func2.body:
                if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                    stmt_bound = set()
                    stmt_reassigned = set()
                    _collect_bindings_and_reassignments(
                        stmt, reassignments2, stmt_bound, stmt_reassigned
                    )
                    bound_after_block2.update(stmt_bound)

            # Variables that are bound in the block but DON'T exist before
            initially_bound2 = bound_in_block2 - bound_before_block2

            # Track variables that need to be returned for block2
            return_variables_block2 = set()

            if initially_bound2:
                block_end_line = pair.block2_range[1]

                if os.getenv("DEBUG_VALIDATION"):
                    print("\n=== Block2 Validation Debug ===")
                    print(f"Function: {pair.function2_name}")
                    print(f"Block lines: {pair.block2_range}")
                    print(f"Bound in block: {bound_in_block2}")
                    print(f"Bound before block: {bound_before_block2}")
                    print(f"Newly bound in block: {initially_bound2}")

                for stmt in func2.body:
                    if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                        uses = self._get_used_names(stmt)
                        if uses & initially_bound2:
                            # Variable is bound in block and used after - will be returned
                            return_variables_block2.update(uses & initially_bound2)
                            if os.getenv("DEBUG_VALIDATION"):
                                print(
                                    f"    RETURN NEEDED: Variable(s) {uses & initially_bound2} will be returned from extracted function"
                                )

                if os.getenv("DEBUG_VALIDATION") and return_variables_block2:
                    print(f"Block2 requires returning: {return_variables_block2}")

        # Check if both blocks are value-producing or both are not
        # Blocks with return_variables are treated as value-producing because
        # we will add return statements for those variables
        value_prod1 = is_value_producing(pair.block1_nodes) or bool(return_variables_block1)
        value_prod2 = is_value_producing(pair.block2_nodes) or bool(return_variables_block2)

        if os.getenv("DEBUG_VALIDATION"):
            print(f"  Value-producing check: block1={value_prod1}, block2={value_prod2}")
            if return_variables_block1:
                print(f"  Block1 has return_variables: {return_variables_block1}")
            if return_variables_block2:
                print(f"  Block2 has return_variables: {return_variables_block2}")

        if value_prod1 != value_prod2:
            if os.getenv("DEBUG_VALIDATION"):
                print("  REJECTED: Value-producing mismatch")
            return None

        # CRITICAL: If blocks are NATURALLY value-producing (have return statements),
        # ensure complete return coverage. This prevents extracting partial control flow.
        # Skip this check for blocks that will have return statements ADDED for return_variables
        if value_prod1 and not return_variables_block1:  # Naturally value-producing
            from .extractor import has_complete_return_coverage

            if not has_complete_return_coverage(pair.block1_nodes):
                if os.getenv("DEBUG_VALIDATION"):
                    print("  REJECTED: Block1 missing complete return coverage")
                return None
            if not has_complete_return_coverage(pair.block2_nodes):
                if os.getenv("DEBUG_VALIDATION"):
                    print("  REJECTED: Block2 missing complete return coverage")
                return None

        # Heuristic: avoid extracting trivial single-line return blocks that just
        # return a previously bound local name (e.g., `return result`). Prefer
        # extracting the preceding computation that produces the value.
        def _is_trivial_return_of_bound_name(block_nodes, bound_before_block, bound_in_block):
            if len(block_nodes) != 1:
                return False
            stmt = block_nodes[0]
            return (
                isinstance(stmt, ast.Return)
                and isinstance(stmt.value, ast.Name)
                and stmt.value.id in bound_before_block
                and stmt.value.id not in bound_in_block
            )

        if _is_trivial_return_of_bound_name(
            pair.block1_nodes, bound_before_block1, bound_in_block1
        ) and _is_trivial_return_of_bound_name(
            pair.block2_nodes, bound_before_block2, bound_in_block2
        ):
            if os.getenv("DEBUG_VALIDATION"):
                print(
                    "  REJECTED: Trivial single-line return blocks (prefer extracting computation)"
                )
            return None

        # Check structural similarity
        if not self._are_structurally_similar(pair.block1_nodes, pair.block2_nodes):
            if os.getenv("DEBUG_VALIDATION"):
                print("  REJECTED: Not structurally similar")
            return None

        # Attempt unification
        blocks = [pair.block1_nodes, pair.block2_nodes]
        hygienic_renames = [{}, {}]

        if os.getenv("DEBUG_VALIDATION"):
            print("  Attempting unification...")

        try:
            substitution = self.unifier.unify_blocks(blocks, hygienic_renames)
        except Exception as e:
            if os.getenv("DEBUG_VALIDATION"):
                print(f"  REJECTED: Unification exception: {e}")
            return None

        if not substitution:
            if os.getenv("DEBUG_VALIDATION"):
                print("  REJECTED: Unification failed (no substitution)")
            return None

        if os.getenv("DEBUG_VALIDATION"):
            print("  ✓ Unification successful")
            print(f"  Substitution: {substitution}")

        # Get enclosing names to avoid shadowing
        enclosing_names = set(root_scope.bindings.keys())

        # Compute free variables for both blocks (variables used but not defined in each block)
        # Use block1's free variables to derive parameters for the extracted function,
        # but validate incomplete lifetimes independently for each block.
        free_vars1 = scope_analyzer.get_free_variables(pair.block1_nodes)
        free_vars2 = (
            pair.scope_analyzer2.get_free_variables(pair.block2_nodes)
            if pair.scope_analyzer2
            else set()
        )

        # CRITICAL VALIDATION: Reject proposals with incomplete variable lifetimes
        # A free variable bound AFTER the block is problematic - we'd be using it before it's defined.
        # However, free variables bound BEFORE the block are OK - they become parameters.
        if free_vars1 & bound_after_block1:
            incomplete_vars = free_vars1 & bound_after_block1
            if os.getenv("DEBUG_VALIDATION"):
                print(
                    f"  REJECTED: Block1 uses variables defined AFTER the block: {incomplete_vars}"
                )
                print("    These variables would be used before they're defined")
            return None

        if free_vars2 & bound_after_block2:
            incomplete_vars = free_vars2 & bound_after_block2
            if os.getenv("DEBUG_VALIDATION"):
                print(
                    f"  REJECTED: Block2 uses variables defined AFTER the block: {incomplete_vars}"
                )
            return None

        # Find all variables used in augmented assignments in block1
        # These variables MUST be passed as parameters even if they appear in substitution
        # because augmented assignments (total += x) READ the variable before writing it
        class AugAssignFinder(ast.NodeVisitor):
            def __init__(self):
                self.aug_assign_targets = set()

            def visit_AugAssign(self, node):
                if isinstance(node.target, ast.Name):
                    self.aug_assign_targets.add(node.target.id)
                self.generic_visit(node)

            def visit_FunctionDef(self, node):
                # Don't descend into nested functions
                pass

            def visit_AsyncFunctionDef(self, node):
                # Don't descend into nested async functions
                pass

        aug_finder = AugAssignFinder()
        for node in pair.block1_nodes:
            aug_finder.visit(node)
        aug_assign_vars = aug_finder.aug_assign_targets

        # Handle augmented assignment variables specially
        # 1) Remove them from substitution so they remain as free variables/parameters
        # 2) Record the name mapping per block for call generation later
        aug_assign_param_mappings = {}
        params_to_remove = []
        for param_name, exprs in list(substitution.param_expressions.items()):
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.Name) and expr.id in aug_assign_vars:
                    if param_name not in aug_assign_param_mappings:
                        aug_assign_param_mappings[param_name] = {}
                    aug_assign_param_mappings[param_name][block_idx] = expr.id
            if 0 in aug_assign_param_mappings.get(param_name, {}):
                params_to_remove.append(param_name)

        # Store the mappings in the substitution object for later use
        if not hasattr(substitution, "aug_assign_mappings"):
            substitution.aug_assign_mappings = {}
        for param_name, block_mappings in aug_assign_param_mappings.items():
            if 0 in block_mappings:
                block1_var_name = block_mappings[0]
                substitution.aug_assign_mappings[block1_var_name] = block_mappings

        # Remove the augmented assignment parameters from substitution
        for param_name in params_to_remove:
            del substitution.param_expressions[param_name]

        # Never parameterize entire f-strings: if any parameter maps to a JoinedStr in
        # the template block (block 0), remove it so the f-string structure is preserved
        fstring_params = []
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.JoinedStr):
                    fstring_params.append(param_name)
                    break
        for param_name in fstring_params:
            del substitution.param_expressions[param_name]

        # Remove variables that have been parameterized from free_vars
        # If a variable was parameterized (e.g., 'user' -> '__param_5'),
        # it's no longer free - it's been replaced by a parameter
        # EXCEPT: variables in augmented assignments MUST remain free variables
        parameterized_vars = set()
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                # Only check the first block (template block)
                if block_idx == 0 and isinstance(expr, ast.Name):
                    # Don't add to parameterized_vars if it's an augmented assignment target
                    if expr.id not in aug_assign_vars:
                        parameterized_vars.add(expr.id)

        # Initialize working free_vars from block1's perspective (template block)
        free_vars = set(free_vars1) - parameterized_vars

        # CRITICAL: Check if any free variables are declared global or nonlocal
        # If a free variable is global/nonlocal, we cannot parameterize it
        # because you cannot have a parameter that is also declared global/nonlocal
        # This would create: SyntaxError: name 'x' is parameter and global
        func1_scope_id = None
        for node, scope in scope_analyzer.node_scopes.items():
            if isinstance(node, ast.FunctionDef) and node.name == pair.function1_name:
                func1_scope_id = scope.scope_id
                break

        if func1_scope_id is not None:
            # Check if any free variables are global or nonlocal in this scope
            global_vars = scope_analyzer.global_vars.get(func1_scope_id, set())
            nonlocal_vars = scope_analyzer.nonlocal_vars.get(func1_scope_id, set())
            problematic = free_vars & (global_vars | nonlocal_vars)
            if problematic:
                # If allowed, relax restrictions when the NAME itself is not assigned,
                # or when it is assigned but the block contains an explicit `global` declaration.
                if self.allow_global_reads_without_assignment:
                    assigned_names: Set[str] = set()
                    declared_global_in_block: Set[str] = set()

                    class AssignTargetVisitor(ast.NodeVisitor):
                        def visit_Assign(self, node):
                            for t in node.targets:
                                if isinstance(t, ast.Name):
                                    assigned_names.add(t.id)
                            self.generic_visit(node)

                        def visit_AugAssign(self, node):
                            if isinstance(node.target, ast.Name):
                                assigned_names.add(node.target.id)
                            self.generic_visit(node)

                        def visit_AnnAssign(self, node):
                            if isinstance(node.target, ast.Name):
                                assigned_names.add(node.target.id)
                            self.generic_visit(node)

                        def visit_Global(self, node):
                            for n in node.names:
                                declared_global_in_block.add(n)
                            # no need to visit deeper for the statement itself

                        def visit_FunctionDef(self, node):
                            # Don't descend into nested functions
                            pass

                        def visit_AsyncFunctionDef(self, node):
                            # Don't descend into nested async functions
                            pass

                    v = AssignTargetVisitor()
                    for n in pair.block1_nodes:
                        v.visit(n)
                    for n in pair.block2_nodes:
                        v.visit(n)

                    # If a problematic name is assigned, allow only if it's declared global within the block
                    assigned_problematic = assigned_names & problematic
                    if assigned_problematic and not assigned_problematic <= declared_global_in_block:
                        return None
                    # Do not parameterize these globals; let them remain free
                    # so the extracted function references the module-level name (and may
                    # include an existing `global` statement if present in the block).
                    free_vars -= problematic
                else:
                    # Strict mode: reject whenever a global/nonlocal free var is present
                    return None

        # Extract function
        try:
            func_def, param_order = self.extractor.extract_function(
                template_block=pair.block1_nodes,
                substitution=substitution,
                free_variables=free_vars,
                enclosing_names=enclosing_names,
                is_value_producing=value_prod1,
                return_variables=list(return_variables_block1),
                function_name="extracted_func",
            )
        except Exception:
            return None

        # Check for orphaned variables before proceeding
        # Need to find the function nodes to check for orphans
        func1 = None
        func2 = None
        for file_path, func, source, analyzer, scope in all_functions:
            if file_path == pair.file_path and func.name == pair.function1_name:
                func1 = func
            if (
                file_path == (pair.file_path2 or pair.file_path)
                and func.name == pair.function2_name
            ):
                func2 = func

        if func1 and func2:
            # Get block indices within their respective function bodies
            indices1 = self._get_block_indices(func1, pair.block1_nodes)
            indices2 = self._get_block_indices(func2, pair.block2_nodes)

            if indices1 and indices2:
                # Get function bodies (skip docstring)
                body1 = func1.body
                start_idx1 = 0
                if (
                    body1
                    and isinstance(body1[0], ast.Expr)
                    and isinstance(body1[0].value, ast.Constant)
                    and isinstance(body1[0].value.value, str)
                ):
                    start_idx1 = 1
                body1 = body1[start_idx1:]

                body2 = func2.body
                start_idx2 = 0
                if (
                    body2
                    and isinstance(body2[0], ast.Expr)
                    and isinstance(body2[0].value, ast.Constant)
                    and isinstance(body2[0].value.value, str)
                ):
                    start_idx2 = 1
                body2 = body2[start_idx2:]

                # Check for orphaned variables in both blocks
                has_orphans1, orphans1 = has_orphaned_variables(body1, indices1)
                has_orphans2, orphans2 = has_orphaned_variables(body2, indices2)

                if has_orphans1 or has_orphans2:
                    # Cannot extract - would create orphaned variable references
                    return None

        # Generate replacement calls
        replacements = []

        # Map block indices to their return variables
        return_vars_by_block = {0: list(return_variables_block1), 1: list(return_variables_block2)}

        for block_idx, (block_range, file_path) in enumerate(
            [
                (pair.block1_range, pair.file_path),
                (pair.block2_range, pair.file_path2 or pair.file_path),
            ]
        ):
            try:
                call_node = self.extractor.generate_call(
                    function_name=func_def.name,
                    block_idx=block_idx,
                    substitution=substitution,
                    param_order=param_order,
                    free_variables=free_vars,
                    is_value_producing=value_prod1,
                    return_variables=return_vars_by_block[block_idx],
                    hygienic_renames=hygienic_renames,
                )
                # Store file_path with replacement for cross-file handling
                replacements.append((block_range, call_node, file_path))
            except Exception:
                return None

        # Determine canonical file for extracted function
        # For cross-file: choose first file
        canonical_file = pair.file_path

        # Create proposal
        is_cross_file = pair.file_path2 is not None and pair.file_path != pair.file_path2

        desc = f"Extract common code from {pair.function1_name}"
        if is_cross_file:
            desc += f" ({Path(pair.file_path).name}) and {pair.function2_name} ({Path(pair.file_path2).name})"
        else:
            desc += f" and {pair.function2_name}"

        proposal = RefactoringProposal(
            file_path=canonical_file,
            extracted_function=func_def,
            replacements=replacements,  # Now includes file_path
            description=desc,
            parameters_count=len(substitution.param_expressions),
            return_variables=list(return_variables_block1),
        )

        return proposal

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        """
        Apply a refactoring proposal to a file.

        Args:
            file_path: Path to file
            proposal: Refactoring proposal

        Returns:
            Modified source code
        """
        # All proposals now use multi-file format
        modified_files = self.apply_refactoring_multi_file(proposal)
        # Return the content for the requested file
        return modified_files.get(file_path, "")

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """
        Apply a cross-file refactoring proposal.

        Args:
            proposal: Refactoring proposal

        Returns:
            Dict mapping file paths to modified source code
        """
        # Group replacements by file
        replacements_by_file = {}
        for item in proposal.replacements:
            if len(item) == 3:
                (start_line, end_line), replacement_node, file_path = item
            else:
                (start_line, end_line), replacement_node = item
                file_path = proposal.file_path

            if file_path not in replacements_by_file:
                replacements_by_file[file_path] = []
            replacements_by_file[file_path].append(((start_line, end_line), replacement_node))

        # Process each file
        modified_files = {}

        for file_path, replacements in replacements_by_file.items():
            # Read original source
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Sort replacements by line number (reverse order)
            replacements = sorted(replacements, key=lambda r: r[0][0], reverse=True)

            # Apply replacements
            for (start_line, end_line), replacement_node in replacements:
                # Validate line numbers
                if start_line < 1 or start_line > len(lines):
                    print(
                        f"Warning: Invalid line range {start_line}-{end_line} for {file_path} (file has {len(lines)} lines)"
                    )
                    print("Skipping this replacement")
                    continue

                if end_line > len(lines):
                    print(
                        f"Warning: End line {end_line} exceeds file length {len(lines)} for {file_path}"
                    )
                    print("Adjusting to end of file")
                    end_line = len(lines)

                replacement_code = ast.unparse(replacement_node)
                indent = self._get_indent(lines[start_line - 1])

                # Split code and indent properly: first line gets indent, subsequent lines keep their relative indentation
                code_lines = replacement_code.split("\n")
                replacement_lines = []
                for i, line in enumerate(code_lines):
                    if i == 0:
                        # First line gets the base indent
                        replacement_lines.append(indent + line + "\n")
                    else:
                        # Subsequent lines maintain their relative indentation from ast.unparse
                        if line.strip():  # Only indent non-empty lines
                            replacement_lines.append(line + "\n")
                        else:
                            replacement_lines.append("\n")

                # Check if we need to preserve blank lines after the replacement
                # to maintain PEP 8 spacing between functions
                trailing_blank_lines = []
                if end_line < len(lines):
                    # Check if there's a function definition after this block
                    next_line_idx = end_line
                    while next_line_idx < len(lines) and not lines[next_line_idx].strip():
                        next_line_idx += 1

                    # If the next non-blank line is a function/class definition,
                    # ensure we have 2 blank lines
                    if next_line_idx < len(lines):
                        next_line = lines[next_line_idx].strip()
                        if (
                            next_line.startswith("def ")
                            or next_line.startswith("class ")
                            or next_line.startswith("async def ")
                        ):
                            # Count existing blank lines between end and next def
                            existing_blanks = next_line_idx - end_line
                            # We need 2 blank lines total
                            if existing_blanks < 2:
                                trailing_blank_lines = ["\n"] * (2 - existing_blanks)

                del lines[start_line - 1 : end_line]
                lines[start_line - 1 : start_line - 1] = replacement_lines + trailing_blank_lines

            # Add extracted function to canonical file only
            if file_path == proposal.file_path:
                func_code = ast.unparse(proposal.extracted_function)
                func_lines = [line + "\n" for line in func_code.split("\n")]
                insert_line = self._find_insert_position(lines)

                # Ensure proper spacing: PEP 8 requires 2 blank lines between top-level functions
                # Add blank lines before the function if needed
                lines_to_insert = []

                # Check if we need blank lines before the function
                if insert_line > 0:
                    # Count existing blank lines before insert position
                    blank_lines_before = 0
                    check_line = insert_line - 1
                    while check_line >= 0 and not lines[check_line].strip():
                        blank_lines_before += 1
                        check_line -= 1

                    # Add blank lines if needed to reach 2
                    if blank_lines_before < 2:
                        lines_to_insert.extend(["\n"] * (2 - blank_lines_before))

                # Add the function itself
                lines_to_insert.extend(func_lines)

                # Add 2 blank lines after the function
                lines_to_insert.extend(["\n", "\n"])

                lines[insert_line:insert_line] = lines_to_insert
            else:
                # Add import statement to other files
                # Calculate the correct module path for importing
                from_path = Path(proposal.file_path)
                to_path = Path(file_path)

                # For files in the same directory, just use the stem
                if from_path.parent == to_path.parent:
                    module_name = from_path.stem
                else:
                    # For nested directories, calculate relative import path
                    # Find common parent and build relative path
                    try:
                        # Get relative path from importing file to target file
                        common_parent = Path(
                            *[p for p in from_path.parts[:-1] if p in to_path.parts[:-1]]
                        )
                        if common_parent == Path("."):
                            # No common parent - use absolute-style import with directory structure
                            module_parts = list(from_path.parent.parts) + [from_path.stem]
                            module_name = ".".join(module_parts)
                        else:
                            # Has common parent - use relative path from common root
                            module_parts = list(from_path.parent.parts) + [from_path.stem]
                            # Find where common parent ends
                            common_depth = len([p for p in common_parent.parts])
                            module_parts = module_parts[common_depth:]
                            module_name = ".".join(module_parts) if module_parts else from_path.stem
                    except (ValueError, IndexError):
                        # Fallback to stem if relative path calculation fails
                        module_name = from_path.stem

                func_name = proposal.extracted_function.name
                import_line = f"from {module_name} import {func_name}\n"

                # Find position to insert import (after existing imports)
                import_pos = self._find_import_position(lines)
                lines.insert(import_pos, import_line)

            modified_files[file_path] = "".join(lines)

        return modified_files

    def _find_import_position(self, lines: List[str]) -> int:
        """Find position to insert a new import statement."""
        in_docstring = False
        docstring_char = None
        last_import_line = 0
        after_docstring = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Track module docstring
            if i == 0 and (stripped.startswith('"""') or stripped.startswith("'''")):
                docstring_char = stripped[:3]
                if stripped.count(docstring_char) < 2:
                    in_docstring = True
                else:
                    # Single-line docstring
                    after_docstring = i + 1
                continue

            if in_docstring:
                if docstring_char in stripped:
                    in_docstring = False
                    after_docstring = i + 1
                continue

            # Track imports
            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import_line = i + 1
                continue

            # If we're past docstring and imports, stop
            if last_import_line > 0 and stripped and not stripped.startswith("#"):
                break

        # Insert after last import, or after docstring if no imports
        if last_import_line > 0:
            return last_import_line
        return after_docstring

    def _get_indent(self, line: str) -> str:
        """Get the indentation of a line."""
        return line[: len(line) - len(line.lstrip())]

    def _find_insert_position(self, lines: List[str]) -> int:
        """
        Find position to insert extracted function at END of file.

        This prevents line number shifts when multiple refactorings are applied,
        making sequential refactorings more robust.
        """
        # Insert at the end of the file
        # Find the last non-blank line
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                # Insert after this line
                return i + 1

        # Empty file - insert at beginning
        return 0

    def _get_block_indices(
        self, function: ast.FunctionDef, block_nodes: List[ast.AST]
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
        body = function.body
        start_idx = 0
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            start_idx = 1
        body = body[start_idx:]

        # Match by line numbers
        block_start_line = block_nodes[0].lineno
        block_end_line = (
            block_nodes[-1].end_lineno
            if hasattr(block_nodes[-1], "end_lineno")
            else block_nodes[-1].lineno
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

    def _are_structurally_similar(
        self, block1: List[ast.AST], block2: List[ast.AST], threshold: float = 0.6
    ) -> bool:
        """
        Check if two blocks are structurally similar enough to attempt unification.

        This does a rough structural comparison to filter out obviously different blocks.

        Args:
            block1: First block
            block2: Second block
            threshold: Similarity threshold (0.0 to 1.0)

        Returns:
            True if blocks are similar enough
        """
        if len(block1) != len(block2):
            return False

        total_nodes = 0
        matching_nodes = 0

        for stmt1, stmt2 in zip(block1, block2):
            # Compare AST structure
            nodes1 = list(ast.walk(stmt1))
            nodes2 = list(ast.walk(stmt2))

            # Must have similar number of nodes
            if abs(len(nodes1) - len(nodes2)) / max(len(nodes1), len(nodes2)) > 0.3:
                return False

            # Count matching node types
            types1 = [type(n).__name__ for n in nodes1]
            types2 = [type(n).__name__ for n in nodes2]

            # Count common types
            from collections import Counter

            counter1 = Counter(types1)
            counter2 = Counter(types2)

            common = sum((counter1 & counter2).values())
            total = max(len(types1), len(types2))

            total_nodes += total
            matching_nodes += common

        if total_nodes == 0:
            return False

        similarity = matching_nodes / total_nodes
        return similarity >= threshold

    def refactor_to_fixed_point(
        self, file_path: str, max_iterations: int = 10
    ) -> Tuple[str, int, List[str]]:
        """
        Apply refactorings iteratively until a fixed point is reached.

        This method applies refactorings one at a time, re-analyzing after each
        application. This prevents the sequential corruption bug where applying
        multiple refactorings at once causes line number misalignment.

        Args:
            file_path: Path to file to refactor
            max_iterations: Maximum iterations to prevent infinite loops

        Returns:
            Tuple of (final_code, num_refactorings_applied, descriptions)
        """
        current_code = Path(file_path).read_text()
        num_applied = 0
        descriptions = []

        for iteration in range(max_iterations):
            # Write current code to file for analysis
            with open(file_path, "w") as f:
                f.write(current_code)

            # Analyze for refactoring opportunities
            proposals = self.analyze_file(file_path)

            if not proposals:
                # Fixed point reached - no more refactorings found
                break

            # Apply only the first proposal
            proposal = proposals[0]
            current_code = self.apply_refactoring(file_path, proposal)
            num_applied += 1
            descriptions.append(proposal.description)

        return current_code, num_applied, descriptions

    def refactor_directory_to_fixed_point(
        self, input_dir: str, output_dir: str, max_iterations: int = 10
    ) -> Dict[str, Tuple[int, List[str]]]:
        """
        Apply refactorings to all files in a directory until fixed point.

        Args:
            input_dir: Input directory path
            output_dir: Output directory path
            max_iterations: Maximum iterations per file

        Returns:
            Dictionary mapping file paths to (num_refactorings, descriptions)
        """
        from pathlib import Path
        import shutil

        input_path = Path(input_dir)
        output_path = Path(output_dir)

        # Create output directory
        output_path.mkdir(parents=True, exist_ok=True)

        # Copy all files to output directory first
        if input_path != output_path:
            for item in input_path.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(input_path)
                    output_file = output_path / rel_path
                    output_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, output_file)

        # Process each Python file
        results = {}
        for py_file in output_path.rglob("*.py"):
            if py_file.name.startswith("."):
                continue

            final_code, num_applied, descriptions = self.refactor_to_fixed_point(
                str(py_file), max_iterations
            )

            # Write final result
            with open(py_file, "w") as f:
                f.write(final_code)

            if num_applied > 0:
                results[str(py_file)] = (num_applied, descriptions)

        return results


# Utility functions for overlap filtering


def get_affected_lines(proposal: RefactoringProposal) -> Set[Tuple[str, int]]:
    """
    Get all (file_path, line_number) tuples affected by a proposal.

    This is used for overlap detection - two proposals overlap if they
    affect any of the same lines in the same file.

    Args:
        proposal: A refactoring proposal

    Returns:
        Set of (file_path, line_number) tuples that would be modified
    """
    affected = set()
    for item in proposal.replacements:
        if len(item) == 3:
            (start_line, end_line), _, file_path = item
        else:
            (start_line, end_line), _ = item
            file_path = proposal.file_path

        for line_num in range(start_line, end_line + 1):
            affected.add((file_path, line_num))

    return affected


def filter_overlapping_proposals(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """
    Filter proposals to remove overlaps, keeping the best ones.

    When multiple proposals affect overlapping lines, this function selects
    a subset of non-overlapping proposals. Larger proposals (affecting more
    lines) are preferred over smaller ones.

    Strategy:
    1. Sort proposals by size (total lines affected), largest first
    2. Greedily select proposals that don't overlap with already-selected ones

    Args:
        proposals: List of refactoring proposals

    Returns:
        List of non-overlapping proposals, sorted by size (largest first)

    Example:
        If proposals affect lines [1-7], [1-6], and [2-7], only [1-7]
        would be selected as it's the largest and the others overlap with it.
    """
    if not proposals:
        return []

    def proposal_size(p: RefactoringProposal) -> int:
        """Calculate total lines affected by a proposal."""
        total_lines = 0
        for item in p.replacements:
            if len(item) == 3:
                (start_line, end_line), _, _ = item
            else:
                (start_line, end_line), _ = item
            total_lines += end_line - start_line + 1
        return total_lines

    # Sort by size (larger first)
    sorted_proposals = sorted(proposals, key=proposal_size, reverse=True)

    # Greedy selection: take largest non-overlapping proposals
    selected = []
    used_lines = set()

    for proposal in sorted_proposals:
        affected = get_affected_lines(proposal)

        # Check if this proposal overlaps with already selected ones
        if not (affected & used_lines):
            selected.append(proposal)
            used_lines.update(affected)

    return selected
