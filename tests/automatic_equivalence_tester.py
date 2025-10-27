"""
Automatic observational equivalence testing.

This module automatically tests that ALL refactored functions from ALL example
files behave identically to their original versions by:

1. Identifying which functions were refactored from proposal descriptions
2. Automatically generating test inputs based on function signatures
3. Testing observational equivalence with comprehensive inputs
"""

import ast
import inspect
import re
from typing import List, Tuple, Dict, Any, Optional, Set
from pathlib import Path

from tests.test_observational_equivalence import (
    execute_function,
    compare_function_behavior,
    FunctionExecutionResult
)


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
    pattern = r'from\s+(\w+)(?:\s+\([^)]+\))?\s+and\s+(\w+)(?:\s+\([^)]+\))?'
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


def analyze_parameter_usage(func_def: ast.FunctionDef, param_name: str) -> Optional[str]:
    """
    Analyze how a parameter is used in the function body to infer its type.

    Args:
        func_def: AST FunctionDef node
        param_name: Name of the parameter to analyze

    Returns:
        Usage pattern like 'tuple_unpacking', 'dict_access', 'list_iter', etc.
    """
    for node in ast.walk(func_def):
        # Check for tuple unpacking in for loops: for a, b in param:
        if isinstance(node, ast.For):
            if isinstance(node.target, ast.Tuple) and isinstance(node.iter, ast.Name):
                if node.iter.id == param_name:
                    # Count how many variables are unpacked
                    num_elements = len(node.target.elts)
                    return f'tuple_unpacking_{num_elements}'

        # Check for dictionary access: param['key'] or param.get('key')
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == param_name:
                return 'dict_access'

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == param_name:
                    if node.func.attr in ['get', 'keys', 'values', 'items']:
                        return 'dict_methods'

        # Check for list/iterable iteration: for item in param:
        if isinstance(node, ast.For):
            if isinstance(node.iter, ast.Name) and node.iter.id == param_name:
                if not isinstance(node.target, ast.Tuple):
                    return 'list_iter'

    return None


def generate_test_values_for_type(
    param_name: str,
    annotation: Optional[ast.AST] = None,
    usage_pattern: Optional[str] = None
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
        if usage_pattern.startswith('tuple_unpacking_'):
            # Extract number of elements from pattern like 'tuple_unpacking_2'
            num_elements = int(usage_pattern.split('_')[-1])

            if num_elements == 2:
                # Common case: pairs like (key, value)
                return [
                    [],  # Empty list
                    [('a', 1), ('b', 2)],  # String keys, int values
                    [('x', 'foo'), ('y', 'bar')],  # String keys and values
                    [(1, 10), (2, 20)],  # Integer pairs
                    [('key1', None), ('key2', 100)],  # With None values
                ]
            else:
                # General tuple unpacking
                if num_elements == 3:
                    return [
                        [],
                        [(1, 2, 3), (4, 5, 6)],
                        [('a', 'b', 'c')],
                    ]
                else:
                    # Default tuples
                    sample_tuple = tuple(range(num_elements))
                    return [[], [sample_tuple, sample_tuple]]

        elif usage_pattern in ['dict_access', 'dict_methods']:
            return [
                {},
                {'key': 'value'},
                {'a': 1, 'b': 2},
                {'id': 123, 'name': 'test'},
                {'x': None, 'y': 100}
            ]

        elif usage_pattern == 'list_iter':
            return [
                [],
                [1, 2, 3],
                [0],
                ['a', 'b', 'c'],
                [10, 20, 30, 40]
            ]

    # Try to infer type from annotation
    if annotation:
        type_name = ast.unparse(annotation) if annotation else None

        if type_name:
            type_name = type_name.lower()

            if 'int' in type_name:
                return [0, 1, -1, 10, 100, -5]
            elif 'str' in type_name:
                return ['', 'hello', 'test', 'a', 'Hello World', '123']
            elif 'list' in type_name:
                return [[], [1, 2, 3], [0], ['a', 'b'], [1]]
            elif 'dict' in type_name:
                return [{}, {'key': 'value'}, {'a': 1, 'b': 2}, {'id': 123}]
            elif 'bool' in type_name:
                return [True, False]
            elif 'float' in type_name:
                return [0.0, 1.5, -2.5, 3.14, 100.0]

    # Heuristics based on parameter name
    name_lower = param_name.lower()

    # Check for plural names that suggest lists of structured data
    if name_lower in ['pairs', 'tuples', 'entries']:
        return [
            [],
            [('a', 1), ('b', 2)],
            [('x', 'foo'), ('y', 'bar')],
            [(1, 10), (2, 20)]
        ]

    if 'id' in name_lower or name_lower.endswith('_id'):
        return [0, 1, 123, 456, -1]
    elif 'count' in name_lower or 'num' in name_lower or 'size' in name_lower:
        return [0, 1, 5, 10, 100]
    elif 'name' in name_lower:
        return ['', 'test', 'John', 'Admin', 'a']
    elif 'email' in name_lower:
        return ['test@example.com', 'user@test.com', '', 'invalid']
    elif 'items' in name_lower or 'list' in name_lower or 'values' in name_lower:
        return [[], [1, 2, 3], [0], [10, 20, 30]]
    elif 'data' in name_lower or 'dict' in name_lower or 'config' in name_lower:
        return [{}, {'key': 'value'}, {'id': 1, 'name': 'test'}]
    elif 'text' in name_lower or 'message' in name_lower or 'str' in name_lower:
        return ['', 'hello', 'test message', 'a']
    elif 'flag' in name_lower or 'enabled' in name_lower or 'is_' in name_lower:
        return [True, False]
    elif name_lower in ['x', 'y', 'z', 'n', 'm', 'i', 'j', 'k']:
        return [0, 1, -1, 5, 10, -5]

    # Default: try common types
    return [
        0, 1, -1, 10,  # integers
        '', 'test', 'a',  # strings
        [], [1, 2, 3],  # lists
        {},  # dict
        True, False,  # booleans
    ]


def generate_test_cases_for_function(func_def: ast.FunctionDef) -> List[Tuple[Tuple, Dict]]:
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
        if arg.arg == 'self' or arg.arg == 'cls':
            continue

        # Get type annotation if available
        annotation = arg.annotation if hasattr(arg, 'annotation') else None

        # Analyze how this parameter is used in the function body
        usage_pattern = analyze_parameter_usage(func_def, arg.arg)

        # Generate test values for this parameter using all available information
        values = generate_test_values_for_type(arg.arg, annotation, usage_pattern)
        params.append((arg.arg, values))

    if not params:
        return [((), {})]

    # Generate combinations of test values
    # For now, use a simple strategy: test each parameter with a few values
    test_cases = []

    # Test 1: Use first value for all parameters
    if all(values for _, values in params):
        args = tuple(values[0] for _, values in params)
        test_cases.append((args, {}))

    # Test 2-N: Vary one parameter at a time
    for param_idx, (param_name, values) in enumerate(params):
        for value in values[:3]:  # Test up to 3 values per parameter
            args = []
            for i, (_, param_values) in enumerate(params):
                if i == param_idx:
                    args.append(value)
                else:
                    # Use first value for other parameters
                    args.append(param_values[0] if param_values else None)

            if None not in args:  # Only add if all params have values
                test_cases.append((tuple(args), {}))

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

    return unique_cases[:10]  # Limit to 10 test cases per function


def test_all_refactored_functions(
    original_code: str,
    refactored_code: str,
    proposal_description: str
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
        return True, []  # No functions to test

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
            original_code,
            refactored_code,
            func_name,
            test_cases
        )

        if not all_passed:
            all_errors.append(
                f"Function '{func_name}' failed observational equivalence:\n" +
                "\n".join(f"  {diff}" for diff in differences[:3])  # Limit to first 3 differences
            )

    return len(all_errors) == 0, all_errors


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

            # Test observational equivalence
            all_passed, errors = test_all_refactored_functions(
                original_code,
                refactored_code,
                proposal.description
            )

            if all_passed:
                passed += 1
            else:
                failed += 1
                all_errors.append(f"Proposal {i} ({proposal.description}):")
                all_errors.extend(errors)

        return passed, failed, all_errors

    def test_all_examples(self, examples_dir: str = "test_examples") -> Dict[str, Any]:
        """
        Test all example files automatically.

        Args:
            examples_dir: Directory containing example files

        Returns:
            Dictionary with test results
        """
        examples_path = Path(examples_dir)
        results = {
            'total_files': 0,
            'total_proposals_tested': 0,
            'total_passed': 0,
            'total_failed': 0,
            'file_results': {}
        }

        for py_file in sorted(examples_path.glob('*.py')):
            if py_file.name == '__init__.py' or py_file.name.startswith('.'):
                continue

            results['total_files'] += 1

            passed, failed, errors = self.test_file(str(py_file))

            results['total_proposals_tested'] += (passed + failed)
            results['total_passed'] += passed
            results['total_failed'] += failed

            results['file_results'][py_file.name] = {
                'passed': passed,
                'failed': failed,
                'errors': errors
            }

        return results
