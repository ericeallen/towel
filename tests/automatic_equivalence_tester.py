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

"""
Automatic observational equivalence testing.

This module automatically tests that ALL refactored functions from ALL example
files behave identically to their original versions by:

1. Identifying which functions were refactored from proposal descriptions
2. Automatically generating test inputs based on function signatures
3. Testing observational equivalence with comprehensive inputs
"""

import ast
import re
import tempfile
from typing import List, Tuple, Dict, Any, Optional, TypedDict
from pathlib import Path

from tests.test_observational_equivalence import (
    compare_function_behavior,
)
from tests.edge_case_values import EdgeCaseValues
from tests.equivalence_targets import Callable, affected_functions, invocation_definitions

# The directory of text fixtures for filename parameters, created once and
# reused; it lives under a directory of its own, never at the temp root.
_TEST_FILE_DIRECTORY: Optional[tempfile.TemporaryDirectory[str]] = None
_TEST_FILES: Optional[List[str]] = None

_TEST_FILE_CONTENTS = {
    "empty.txt": "",
    "single_line.txt": "test line\n",
    "multi_line.txt": "line 1\nline 2\nline 3\n",
}


def get_test_files() -> List[str]:
    """
    Get or create temporary test files for filename parameters.

    Returns a list of file paths that can be safely opened and read.
    """
    global _TEST_FILE_DIRECTORY, _TEST_FILES

    if _TEST_FILES is None:
        _TEST_FILE_DIRECTORY = tempfile.TemporaryDirectory(prefix="towel_equivalence_")
        directory = Path(_TEST_FILE_DIRECTORY.name)
        _TEST_FILES = []
        for name, contents in _TEST_FILE_CONTENTS.items():
            path = directory / name
            path.write_text(contents)
            _TEST_FILES.append(str(path))

    return _TEST_FILES


def cleanup_test_files():
    """Clean up temporary test files."""
    global _TEST_FILE_DIRECTORY, _TEST_FILES

    if _TEST_FILE_DIRECTORY is not None:
        _TEST_FILE_DIRECTORY.cleanup()
    _TEST_FILE_DIRECTORY = None
    _TEST_FILES = None


def extract_function_names_from_proposal(description: str) -> List[str]:
    """
    Extract function names from a refactoring proposal description.

    Proposal descriptions look like:
    "Extract common code from process_user_data and process_admin_data"
    "Extract common code from func_a and func_b"

    Args:
        description: Refactoring proposal description

    Returns:
        List of function names that were refactored
    """
    # Pattern: "from <func1> and <func2>" or "from <func1> (<file>) and <func2> (<file>)"
    pattern = r"from\s+(\w+)(?:\s+\([^)]+\))?\s+and\s+(\w+)(?:\s+\([^)]+\))?"
    match = re.search(pattern, description)

    if match:
        return [match.group(1), match.group(2)]

    return []


def get_function_signature(code: str, function_name: str) -> Optional[ast.FunctionDef]:
    """
    Extract the AST FunctionDef for a specific function.

    Args:
        code: Python source code
        function_name: Name of function to find

    Returns:
        AST FunctionDef node or None if not found
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node

    return None


def analyze_parameter_usage(func_def: Callable, param_name: str) -> Optional[str]:
    """
    Analyze how a parameter is used in the function body to infer its type.

    Args:
        func_def: AST FunctionDef node
        param_name: Name of the parameter to analyze

    Returns:
        Usage pattern like 'tuple_unpacking', 'dict_access', 'list_iter', 'range_arg', etc.
    """
    for node in ast.walk(func_def):
        # Check for range() usage: range(param) or range(x, param) or range(x, y, param)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                # Check if param is used as any argument to range()
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id == param_name:
                        return "range_arg"

            # Check for dictionary methods
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == param_name:
                    if node.func.attr in ["get", "keys", "values", "items"]:
                        return "dict_methods"

        # Check for tuple unpacking in for loops: for a, b in param:
        if isinstance(node, ast.For):
            if isinstance(node.target, ast.Tuple) and isinstance(node.iter, ast.Name):
                if node.iter.id == param_name:
                    # Count how many variables are unpacked
                    num_elements = len(node.target.elts)
                    return f"tuple_unpacking_{num_elements}"

            # Check for range(param) in for loop: for i in range(param):
            if isinstance(node.iter, ast.Call):
                if isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range":
                    for arg in node.iter.args:
                        if isinstance(arg, ast.Name) and arg.id == param_name:
                            return "range_arg"

        # Check for dictionary access: param['key'] or param.get('key')
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == param_name:
                return "dict_access"

        # Check for list/iterable iteration: for item in param:
        if isinstance(node, ast.For):
            if isinstance(node.iter, ast.Name) and node.iter.id == param_name:
                if not isinstance(node.target, ast.Tuple):
                    return "list_iter"

    return None


def generate_test_values_for_type(
    param_name: str, annotation: Optional[ast.AST] = None, usage_pattern: Optional[str] = None
) -> List[Any]:
    """
    Generate test values for a parameter based on its type annotation, name, and usage.

    Args:
        param_name: Name of the parameter (used for heuristics)
        annotation: Type annotation AST node (if available)
        usage_pattern: How the parameter is used (from analyze_parameter_usage)

    Returns:
        List of test values to try
    """
    # Use usage pattern analysis first (most specific)
    if usage_pattern:
        if usage_pattern.startswith("tuple_unpacking_"):
            # Extract number of elements from pattern like 'tuple_unpacking_2'
            num_elements = int(usage_pattern.split("_")[-1])

            if num_elements == 2:
                # Common case: pairs like (key, value)
                return [
                    [],  # Empty list
                    [("a", 1), ("b", 2)],  # String keys, int values
                    [("x", "foo"), ("y", "bar")],  # String keys and values
                    [(1, 10), (2, 20)],  # Integer pairs
                    [("key1", None), ("key2", 100)],  # With None values
                ]
            else:
                # General tuple unpacking
                if num_elements == 3:
                    return [
                        [],
                        [(1, 2, 3), (4, 5, 6)],
                        [("a", "b", "c")],
                    ]
                else:
                    # Default tuples
                    sample_tuple = tuple(range(num_elements))
                    return [[], [sample_tuple, sample_tuple]]

        elif usage_pattern in ["dict_access", "dict_methods"]:
            return [
                {},
                {"key": "value"},
                {"a": 1, "b": 2},
                {"id": 123, "name": "test"},
                {"x": None, "y": 100},
            ]

        elif usage_pattern == "list_iter":
            return [[], [1, 2, 3], [0], ["a", "b", "c"], [10, 20, 30, 40]]

        elif usage_pattern == "range_arg":
            # Limited integers for range() to avoid hanging
            # range() with huge values causes performance issues
            return [
                0,  # Empty range
                1,  # Single element
                2,  # Two elements
                5,  # Small range
                10,  # Medium range
                100,  # Large but reasonable
                -1,  # Negative (empty range when used as range(n))
                -5,  # More negative tests
            ]

    # Try to infer type from annotation
    if annotation:
        type_name = ast.unparse(annotation) if annotation else None

        if type_name:
            type_name = type_name.lower()

            # Use comprehensive edge case values for better coverage
            # Note: Keep collections small to avoid performance issues, but full numeric coverage
            # The containers are matched first: ``list[int]`` is a list, and
            # its item type must not make it an int.
            if "list" in type_name:
                edge_lists = EdgeCaseValues.lists()
                # Exclude very large lists (last few with 100+ elements)
                return edge_lists[:10]  # Up to 10 elements max
            elif "dict" in type_name:
                edge_dicts = EdgeCaseValues.dicts()
                # Exclude very large dicts (last one with 100 keys)
                return edge_dicts[:-1]  # All except last
            elif "int" in type_name:
                # Full integer edge cases - large ints don't cause issues unless used in range()
                edge_ints = EdgeCaseValues.integers()
                return edge_ints  # All int edge cases including sys.maxsize, 10**100
            elif "str" in type_name:
                edge_strs = EdgeCaseValues.strings()
                # Exclude only the very long string (last one: 'a' * 1000)
                return edge_strs[:-1]  # All except last
            elif "bool" in type_name:
                return EdgeCaseValues.booleans()
            elif "float" in type_name:
                # Full float edge cases - inf, nan, etc. don't cause performance issues
                edge_floats = EdgeCaseValues.floats()
                return edge_floats  # All float edge cases including nan, inf, sys.float_info.max

    # Heuristics based on parameter name
    name_lower = param_name.lower()

    # Check for filename/file/path parameters - use real test files
    # Match: filename, file_path, filepath, path, file1, file2, outer_file, etc.
    if (
        any(keyword in name_lower for keyword in ["filename", "file_path", "filepath", "path"])
        or name_lower.startswith("file")
        or name_lower.endswith("file")
        or "_file" in name_lower
    ):
        # Use real temporary files that can be safely opened
        test_files = get_test_files()
        return test_files

    # Check for plural names that suggest lists of structured data
    if name_lower in ["pairs", "tuples", "entries"]:
        return [[], [("a", 1), ("b", 2)], [("x", "foo"), ("y", "bar")], [(1, 10), (2, 20)]]

    if "id" in name_lower or name_lower.endswith("_id"):
        # IDs: test edge cases including boundaries - full range safe
        return EdgeCaseValues.integers()[:8]
    elif "count" in name_lower or "num" in name_lower or "size" in name_lower:
        # Counts: focus on boundary values (0, 1, moderate) - avoid large values that might go in range()
        return [0, 1, 5, 10, 100]
    elif "name" in name_lower:
        # Names: include unicode and empty
        return EdgeCaseValues.strings()[:8]
    elif "email" in name_lower:
        return ["test@example.com", "user@test.com", "", "invalid"]
    elif "items" in name_lower or "list" in name_lower or "values" in name_lower:
        # Lists: include edge cases but limit to moderate size (no 1000-element lists)
        return EdgeCaseValues.lists()[:10]
    elif "data" in name_lower or "dict" in name_lower or "config" in name_lower:
        # Dicts: include edge cases but limit size (no 100-key dicts)
        return EdgeCaseValues.dicts()[:-1]
    elif "text" in name_lower or "message" in name_lower or "str" in name_lower:
        # Text: include unicode and edge cases
        return EdgeCaseValues.strings()[:-1]  # All except 1000-char string
    elif "flag" in name_lower or "enabled" in name_lower or "is_" in name_lower:
        return EdgeCaseValues.booleans()
    elif name_lower in ["x", "y", "z", "n", "m", "i", "j", "k"]:
        # Generic numeric variables: use full int edge cases
        return EdgeCaseValues.integers()

    # Default: sample from all types for maximum coverage (smaller set)
    edge_ints = EdgeCaseValues.integers()
    edge_strs = EdgeCaseValues.strings()
    edge_lists = EdgeCaseValues.lists()
    edge_dicts = EdgeCaseValues.dicts()

    return [
        edge_ints[0],
        edge_ints[1],
        edge_ints[2],
        edge_ints[3],  # 0, -0, 1, -1
        edge_strs[0],
        edge_strs[1],
        edge_strs[2],  # '', 'a', 'A'
        edge_lists[0],
        edge_lists[1],  # [], [0]
        edge_dicts[0],  # {}
        True,
        False,
    ]


TestCase = Tuple[Tuple[object, ...], Dict[str, object]]


def generate_test_cases_for_function(func_def: Callable) -> List[TestCase]:
    """
    Generate test cases (args, kwargs) for a function based on its signature.

    Args:
        func_def: AST FunctionDef node

    Returns:
        List of (args, kwargs) tuples to test
    """
    if not func_def or not func_def.args:
        return []

    # Get parameter information with usage analysis
    params = []
    for arg in func_def.args.args:
        if arg.arg == "self" or arg.arg == "cls":
            continue

        # Get type annotation if available
        annotation = arg.annotation if hasattr(arg, "annotation") else None

        # Analyze how this parameter is used in the function body
        usage_pattern = analyze_parameter_usage(func_def, arg.arg)

        # Generate test values for this parameter using all available information
        values = generate_test_values_for_type(arg.arg, annotation, usage_pattern)
        params.append((arg.arg, values))

    if not params:
        return [((), {})]

    # Generate combinations of test values
    # For now, use a simple strategy: test each parameter with a few values
    test_cases: List[TestCase] = []

    # Test 1: Use first value for all parameters
    if all(values for _, values in params):
        args = tuple(values[0] for _, values in params)
        test_cases.append((args, {}))

    # Test 2-N: Vary one parameter at a time
    for param_idx, (param_name, values) in enumerate(params):
        for value in values:  # Test ALL values per parameter for comprehensive edge-case coverage
            varied_args: List[object] = []
            for i, (_, param_values) in enumerate(params):
                if i == param_idx:
                    varied_args.append(value)
                else:
                    # Use first value for other parameters
                    varied_args.append(param_values[0] if param_values else None)

            if None not in varied_args:  # Only add if all params have values
                test_cases.append((tuple(varied_args), {}))

    # Remove duplicates while preserving order
    seen = set()
    unique_cases = []
    for case in test_cases:
        # Convert to hashable form for deduplication
        try:
            key = str(case)
            if key not in seen:
                seen.add(key)
                unique_cases.append(case)
        except:
            # If not hashable, just add it
            unique_cases.append(case)

    return unique_cases  # Return ALL test cases for maximum edge-case coverage


def test_all_refactored_functions(
    original_code: str, refactored_code: str, proposal_description: str
) -> Tuple[bool, List[str]]:
    """
    Automatically test all functions mentioned in a refactoring proposal.

    Args:
        original_code: Original Python source code
        refactored_code: Refactored Python source code
        proposal_description: Description of the refactoring

    Returns:
        Tuple of (all_passed, error_messages)
    """
    # Extract function names from proposal
    function_names = extract_function_names_from_proposal(proposal_description)

    if not function_names:
        return False, ["No functions identified in proposal; equivalence was not tested"]

    all_errors = []

    for func_name in function_names:
        # Get function signature from original code
        func_def = get_function_signature(original_code, func_name)

        if not func_def:
            all_errors.append(f"Could not find function '{func_name}' in original code")
            continue

        # Generate test cases
        test_cases = generate_test_cases_for_function(func_def)

        if not test_cases:
            all_errors.append(f"Could not generate test cases for '{func_name}'")
            continue

        # Test observational equivalence
        all_passed, differences = compare_function_behavior(
            original_code, refactored_code, func_name, test_cases
        )

        if not all_passed:
            all_errors.append(
                f"Function '{func_name}' failed observational equivalence:\n"
                + "\n".join(f"  {diff}" for diff in differences[:1])  # Show only first difference
            )

    return len(all_errors) == 0, all_errors


def prepare_target_cases(source: str, target: str) -> Tuple[str, str, List[TestCase]]:
    """Generate cases for an actual function or a class-method invocation adapter."""
    adapter, name, definition, constructor = invocation_definitions(source, target)
    method_cases = generate_test_cases_for_function(definition)
    if not adapter:
        return adapter, name, method_cases
    constructor_cases = (
        generate_test_cases_for_function(constructor) if constructor is not None else [((), {})]
    )
    if definition.name == "__init__":
        method_cases = [((), {})]
    return (
        adapter,
        name,
        [
            ((constructor_args, method_args), {})
            for constructor_args, _ in constructor_cases
            for method_args, _ in method_cases
        ],
    )


def check_affected_functions(
    original_code: str, refactored_code: str, targets: Tuple[str, ...]
) -> Tuple[bool, List[str]]:
    """Verify explicitly selected original callables without parsing descriptions."""
    if not targets:
        return False, ["No affected callables selected"]
    errors: List[str] = []
    for target in targets:
        try:
            adapter, callable_name, cases = prepare_target_cases(original_code, target)
            passed, differences = compare_function_behavior(
                original_code + adapter, refactored_code + adapter, callable_name, cases
            )
            if not passed:
                errors.extend(f"{target}: {difference}" for difference in differences[:1])
        except (ValueError, SyntaxError) as error:
            errors.append(f"{target}: equivalence was not tested: {error}")
    return not errors, errors


class FileResult(TypedDict):
    passed: int
    failed: int
    errors: List[str]


class ExampleResults(TypedDict):
    total_files: int
    total_proposals_tested: int
    total_passed: int
    total_failed: int
    file_results: Dict[str, FileResult]


class AutomaticEquivalenceTester:
    """
    Automatically tests observational equivalence for refactorings.

    This class can test entire example files automatically, generating
    appropriate test inputs and verifying all refactored functions.
    """

    def __init__(self, engine):
        """
        Initialize the tester.

        Args:
            engine: UnificationRefactorEngine instance
        """
        self.engine = engine

    def test_file(self, file_path: str) -> Tuple[int, int, List[str]]:
        """
        Test all refactorings for a file automatically.

        Args:
            file_path: Path to Python file to test

        Returns:
            Tuple of (passed_count, failed_count, error_messages)
        """
        original_code = Path(file_path).read_text()

        # Get refactoring proposals
        proposals = self.engine.analyze_file(file_path)

        if not proposals:
            return 0, 0, []

        passed = 0
        failed = 0
        all_errors = []

        for i, proposal in enumerate(proposals, 1):  # Test ALL proposals
            # Apply refactoring
            try:
                refactored_code = self.engine.apply_refactoring(file_path, proposal)
            except Exception as e:
                all_errors.append(f"Proposal {i} ({proposal.description}): Failed to apply - {e}")
                failed += 1
                continue

            # Every replacement, including additional clustered occurrences, must be tested.
            try:
                targets = affected_functions(proposal, {Path(file_path): original_code})
                all_passed, errors = check_affected_functions(
                    original_code, refactored_code, targets[Path(file_path).resolve()]
                )
            except (ValueError, SyntaxError) as error:
                all_passed, errors = False, [f"Equivalence was not tested: {error}"]

            if all_passed:
                passed += 1
            else:
                failed += 1
                all_errors.append(f"Proposal {i} ({proposal.description}):")
                all_errors.extend(errors)

        return passed, failed, all_errors

    def test_all_examples(
        self, examples_dir: str = "test_examples", verbose: bool = True
    ) -> ExampleResults:
        """
        Test all example files automatically.

        Args:
            examples_dir: Directory containing example files
            verbose: Print progress as files are tested

        Returns:
            Dictionary with test results
        """
        examples_path = Path(examples_dir)
        results: ExampleResults = {
            "total_files": 0,
            "total_proposals_tested": 0,
            "total_passed": 0,
            "total_failed": 0,
            "file_results": {},
        }

        all_files = [
            f
            for f in sorted(examples_path.glob("*.py"))
            if f.name != "__init__.py" and not f.name.startswith(".")
        ]

        for i, py_file in enumerate(all_files, 1):
            results["total_files"] += 1

            if verbose:
                print(f"[{i}/{len(all_files)}] Testing {py_file.name}...", end=" ", flush=True)

            passed, failed, errors = self.test_file(str(py_file))

            results["total_proposals_tested"] += passed + failed
            results["total_passed"] += passed
            results["total_failed"] += failed

            results["file_results"][py_file.name] = {
                "passed": passed,
                "failed": failed,
                "errors": errors,
            }

            if verbose:
                total = passed + failed
                if total > 0:
                    print(f"{passed}/{total} passed")
                else:
                    print("no proposals")

        return results
