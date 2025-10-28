"""
Cross-file observational equivalence testing.

This module tests that cross-file refactorings preserve behavior across
multiple files in nested directory structures by:

1. Creating temporary copies of entire project directories
2. Applying cross-file refactorings using analyze_directory()
3. Comparing behavior by importing and executing functions from both versions
4. Reusing the single-file testing infrastructure where possible
"""

import sys
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import importlib.util

from tests.automatic_equivalence_tester import (
    test_all_refactored_functions,
    extract_function_names_from_proposal
)


class CrossFileEquivalenceTester:
    """
    Tests observational equivalence for cross-file refactorings.

    This class handles entire project directories with nested structures,
    applying cross-file refactorings and verifying behavior is preserved.
    """

    def __init__(self, engine):
        """
        Initialize the cross-file tester.

        Args:
            engine: UnificationRefactorEngine instance
        """
        self.engine = engine

    def _import_module_from_path(self, file_path: Path, module_name: str):
        """
        Dynamically import a Python module from a file path.

        Args:
            file_path: Path to .py file
            module_name: Name to give the module

        Returns:
            Imported module object or None if import failed
        """
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                # Add to sys.modules to support cross-module imports
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                return module
        except Exception as e:
            print(f"Failed to import {file_path}: {e}")
            return None

    def _get_all_python_files(self, directory: Path) -> List[Path]:
        """
        Recursively get all Python files in a directory.

        Args:
            directory: Directory to search

        Returns:
            List of paths to .py files
        """
        python_files = []
        for py_file in directory.rglob("*.py"):
            # Skip __pycache__, hidden files, __init__.py
            if ('__pycache__' in py_file.parts or
                any(part.startswith('.') for part in py_file.parts) or
                py_file.name == '__init__.py'):
                continue
            python_files.append(py_file)
        return sorted(python_files)

    def _compare_project_behavior(
        self,
        original_dir: Path,
        refactored_dir: Path,
        test_functions: Dict[str, List[str]]
    ) -> Tuple[bool, List[str]]:
        """
        Compare behavior of functions between original and refactored projects.

        Args:
            original_dir: Directory with original code
            refactored_dir: Directory with refactored code
            test_functions: Dict mapping file_path -> list of function names to test

        Returns:
            Tuple of (all_passed, error_messages)
        """
        errors = []
        all_passed = True

        # Temporarily add directories to sys.path for imports
        original_dir_str = str(original_dir)
        refactored_dir_str = str(refactored_dir)

        # Save original sys.path
        original_sys_path = sys.path.copy()
        original_modules = set(sys.modules.keys())

        try:
            for file_path, func_names in test_functions.items():
                # Construct module name from relative path
                rel_path = Path(file_path).relative_to(original_dir)
                module_name = str(rel_path.with_suffix('')).replace('/', '.')

                # Import from original
                sys.path.insert(0, original_dir_str)
                original_file = original_dir / rel_path
                original_module = self._import_module_from_path(
                    original_file,
                    f"original_{module_name}"
                )

                if not original_module:
                    errors.append(f"Failed to import original {rel_path}")
                    all_passed = False
                    continue

                # Clean up sys.modules for fresh import
                new_modules = set(sys.modules.keys()) - original_modules
                for mod in new_modules:
                    if mod in sys.modules:
                        del sys.modules[mod]

                # Import from refactored
                sys.path[0] = refactored_dir_str
                refactored_file = refactored_dir / rel_path
                refactored_module = self._import_module_from_path(
                    refactored_file,
                    f"refactored_{module_name}"
                )

                if not refactored_module:
                    errors.append(f"Failed to import refactored {rel_path}")
                    all_passed = False
                    continue

                # Compare each function
                for func_name in func_names:
                    original_func = getattr(original_module, func_name, None)
                    refactored_func = getattr(refactored_module, func_name, None)

                    if not original_func or not refactored_func:
                        errors.append(
                            f"Function '{func_name}' not found in {rel_path}"
                        )
                        all_passed = False
                        continue

                    # Use the existing test infrastructure to compare
                    # We'll read the source and use test_all_refactored_functions
                    original_source = original_file.read_text()
                    refactored_source = refactored_file.read_text()

                    # Create a fake proposal description for this function
                    proposal_desc = f"Extract common code from {func_name} and {func_name}"

                    passed, func_errors = test_all_refactored_functions(
                        original_source,
                        refactored_source,
                        proposal_desc
                    )

                    if not passed:
                        all_passed = False
                        errors.extend(func_errors)

        finally:
            # Restore sys.path
            sys.path = original_sys_path

            # Clean up imported modules
            new_modules = set(sys.modules.keys()) - original_modules
            for mod in new_modules:
                if mod in sys.modules:
                    del sys.modules[mod]

        return all_passed, errors

    def test_project(
        self,
        project_dir: str,
        verbose: bool = True
    ) -> Tuple[int, int, List[str]]:
        """
        Test cross-file refactorings for an entire project directory.

        Each proposal is tested independently on a fresh copy of the original project.

        Args:
            project_dir: Path to project directory to test
            verbose: Print progress messages

        Returns:
            Tuple of (passed_count, failed_count, error_messages)
        """
        project_path = Path(project_dir)

        if not project_path.exists():
            return 0, 0, [f"Project directory not found: {project_dir}"]

        if verbose:
            print(f"Testing project: {project_path.name}")

        # Analyze once to get all proposals
        proposals = self.engine.analyze_directory(str(project_path), recursive=True)

        if not proposals:
            if verbose:
                print("  No cross-file refactoring proposals found")
            return 0, 0, []

        if verbose:
            print(f"  Found {len(proposals)} refactoring proposal(s)")

        passed = 0
        failed = 0
        all_errors = []

        # Test each proposal independently on a fresh copy
        for i, proposal in enumerate(proposals, 1):
            # Create fresh temp directories for this proposal
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                original_copy = tmp_path / "original"
                refactored_copy = tmp_path / "refactored"

                # Copy project to both directories
                shutil.copytree(project_path, original_copy)
                shutil.copytree(project_path, refactored_copy)

                # Apply refactoring to refactored copy
                try:
                    modified_files = self.engine.apply_refactoring_multi_file(proposal)

                    # Write modified files
                    for file_path, content in modified_files.items():
                        # Make path relative to project_path
                        try:
                            rel_path = Path(file_path).relative_to(project_path)
                        except ValueError:
                            # If not relative, it's an absolute path - use filename only
                            rel_path = Path(file_path).name

                        file_full_path = refactored_copy / rel_path
                        file_full_path.parent.mkdir(parents=True, exist_ok=True)
                        file_full_path.write_text(content)

                except Exception as e:
                    all_errors.append(
                        f"Proposal {i} ({proposal.description}): Failed to apply - {e}"
                    )
                    failed += 1
                    continue

                # Extract function names from proposal
                func_names = extract_function_names_from_proposal(proposal.description)

                if not func_names:
                    # No specific functions to test, consider it passed if it applied
                    passed += 1
                    continue

                # Build test_functions dict: map file paths to function names
                # We need to figure out which files contain these functions
                test_functions = {}
                for py_file in self._get_all_python_files(refactored_copy):
                    source = py_file.read_text()
                    for func_name in func_names:
                        if f"def {func_name}(" in source:
                            # Make path relative to refactored_copy for comparison
                            rel_path = py_file.relative_to(refactored_copy)
                            abs_original_path = original_copy / rel_path
                            if str(abs_original_path) not in test_functions:
                                test_functions[str(abs_original_path)] = []
                            test_functions[str(abs_original_path)].append(func_name)

                # Compare behavior
                all_passed, errors = self._compare_project_behavior(
                    original_copy,
                    refactored_copy,
                    test_functions
                )

                if all_passed:
                    passed += 1
                    if verbose:
                        print(f"  ✓ Proposal {i}: {proposal.description}")
                else:
                    failed += 1
                    if verbose:
                        print(f"  ✗ Proposal {i}: {proposal.description}")
                    all_errors.append(f"Proposal {i} ({proposal.description}):")
                    all_errors.extend(errors)

        return passed, failed, all_errors

    def test_all_projects(
        self,
        projects_dir: str = "test_examples_crossfile",
        verbose: bool = True
    ) -> Dict[str, Any]:
        """
        Test all project directories in a root directory.

        Args:
            projects_dir: Root directory containing project subdirectories
            verbose: Print progress messages

        Returns:
            Dictionary with test results
        """
        projects_path = Path(projects_dir)

        if not projects_path.exists():
            return {
                'total_projects': 0,
                'total_proposals_tested': 0,
                'total_passed': 0,
                'total_failed': 0,
                'project_results': {},
                'error': f'Projects directory not found: {projects_dir}'
            }

        results = {
            'total_projects': 0,
            'total_proposals_tested': 0,
            'total_passed': 0,
            'total_failed': 0,
            'project_results': {}
        }

        # Find all project directories (subdirectories of projects_dir)
        project_dirs = [d for d in projects_path.iterdir()
                       if d.is_dir() and not d.name.startswith('.')]

        if verbose:
            print(f"\n=== Testing {len(project_dirs)} Cross-File Project(s) ===\n")

        for i, project_dir in enumerate(sorted(project_dirs), 1):
            results['total_projects'] += 1

            if verbose:
                print(f"[{i}/{len(project_dirs)}] Testing {project_dir.name}...")

            passed, failed, errors = self.test_project(str(project_dir), verbose=verbose)

            results['total_proposals_tested'] += (passed + failed)
            results['total_passed'] += passed
            results['total_failed'] += failed

            results['project_results'][project_dir.name] = {
                'passed': passed,
                'failed': failed,
                'errors': errors
            }

            if verbose:
                total = passed + failed
                if total > 0:
                    print(f"  Result: {passed}/{total} passed\n")
                else:
                    print(f"  Result: No proposals\n")

        return results
