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
from typing import Dict, List, Tuple, Any
import importlib
from contextlib import contextmanager
from typing import Iterator

from tests.test_observational_equivalence import (
    FunctionExecutionResult,
    compare_executions,
    execute_callable,
)

from tests.automatic_equivalence_tester import (
    prepare_target_cases,
)

from tests.equivalence_targets import affected_functions


@contextmanager
def _isolated_project_imports(project: Path, roots: Tuple[Path, Path]) -> Iterator[None]:
    """Temporarily isolate project modules, including pre-existing name collisions.

    The harness runs serially: sys.path and sys.modules are process-global. Restore
    both even if importing or executing user code raises.
    """
    saved_path = sys.path[:]
    saved_modules = sys.modules.copy()
    top_level_names = {
        path.stem if path.is_file() else path.name
        for root in roots
        for path in root.iterdir()
        if not path.name.startswith(".") and (path.is_dir() or path.suffix == ".py")
    }
    try:
        for name in tuple(sys.modules):
            if name.split(".")[0] in top_level_names:
                del sys.modules[name]
        sys.path[:] = [str(project)] + [p for p in saved_path if p not in map(str, roots)]
        importlib.invalidate_caches()
        yield
    finally:
        sys.path[:] = saved_path
        for name in tuple(sys.modules):
            if name not in saved_modules:
                del sys.modules[name]
        sys.modules.update(saved_modules)
        importlib.invalidate_caches()


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
            if (
                "__pycache__" in py_file.parts
                or any(part.startswith(".") for part in py_file.parts)
                or py_file.name == "__init__.py"
            ):
                continue
            python_files.append(py_file)
        return sorted(python_files)

    def _compare_project_behavior(
        self, original_dir: Path, refactored_dir: Path, test_functions: Dict[str, List[str]]
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
        errors: List[str] = []
        if not test_functions or not any(test_functions.values()):
            return False, ["No functions selected; equivalence was not tested"]
        roots = (original_dir, refactored_dir)
        for file_path, function_names in test_functions.items():
            relative_path = Path(file_path).relative_to(original_dir)
            module_name = ".".join(relative_path.with_suffix("").parts)
            original_source = (original_dir / relative_path).read_text()
            for function_name in function_names:
                try:
                    adapter, callable_name, cases = prepare_target_cases(
                        original_source, function_name
                    )
                except (ValueError, SyntaxError) as error:
                    errors.append(f"{relative_path}:{function_name}: {error}")
                    continue

                def execute_project(
                    project: Path, args: Tuple[object, ...], kwargs: Dict[str, object]
                ) -> FunctionExecutionResult:
                    with _isolated_project_imports(project, roots):
                        # Import and execute inside the same environment: function-local imports
                        # must resolve against the version whose behavior is being measured.
                        loaded_function = False
                        module_namespace: Dict[str, object] = {}

                        def import_and_call(*call_args: object, **call_kwargs: object) -> object:
                            nonlocal loaded_function, module_namespace
                            module = importlib.import_module(module_name)
                            module_namespace = module.__dict__
                            if adapter:
                                exec(adapter, module.__dict__)
                            function = getattr(module, callable_name)
                            if not callable(function):
                                raise TypeError(f"{module_name}.{function_name} is not callable")
                            loaded_function = True
                            return function(*call_args, **call_kwargs)

                        result = execute_callable(import_and_call, args, kwargs)
                        if adapter:
                            result.target_invoked = (
                                module_namespace.get(callable_name + "_invoked", True) is not False
                            )
                        if not loaded_function:
                            raise ValueError(
                                f"Could not load {module_name}.{function_name}: {result.exception}; "
                                "equivalence was not tested"
                            )
                        if callable(result.return_value):
                            raise ValueError(
                                "Cross-file returned callables require persistent import isolation; "
                                "equivalence was not tested"
                            )
                        return result

                try:
                    _, differences = compare_executions(
                        lambda args, kwargs: execute_project(original_dir, args, kwargs),
                        lambda args, kwargs: execute_project(refactored_dir, args, kwargs),
                        cases,
                    )
                    errors.extend(
                        f"{relative_path}:{function_name}: {diff}" for diff in differences
                    )
                except ValueError as error:
                    errors.append(f"{relative_path}:{function_name}: {error}")
        return not errors, errors

    def test_project(self, project_dir: str, verbose: bool = True) -> Tuple[int, int, List[str]]:
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
                        rel_path = Path(file_path).resolve().relative_to(project_path.resolve())

                        file_full_path = refactored_copy / rel_path
                        file_full_path.parent.mkdir(parents=True, exist_ok=True)
                        file_full_path.write_text(content)

                except Exception as e:
                    all_errors.append(
                        f"Proposal {i} ({proposal.description}): Failed to apply - {e}"
                    )
                    failed += 1
                    continue

                # Resolve affected callables in the original files, before generated imports
                # and helpers shift source positions. Descriptions are presentation only.
                try:
                    original_sources = {
                        Path(replacement.file_path or proposal.file_path)
                        .resolve(): Path(replacement.file_path or proposal.file_path)
                        .read_text()
                        for replacement in proposal.replacements
                    }
                    selected = affected_functions(proposal, original_sources)
                    test_functions = {
                        str(original_copy / path.relative_to(project_path.resolve())): list(names)
                        for path, names in selected.items()
                    }
                except (ValueError, SyntaxError, OSError) as error:
                    failed += 1
                    all_errors.append(f"Proposal {i}: equivalence was not tested: {error}")
                    continue

                # Compare behavior
                all_passed, errors = self._compare_project_behavior(
                    original_copy, refactored_copy, test_functions
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
        self, projects_dir: str = "test_examples_crossfile", verbose: bool = True
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
                "total_projects": 0,
                "total_proposals_tested": 0,
                "total_passed": 0,
                "total_failed": 0,
                "project_results": {},
                "error": f"Projects directory not found: {projects_dir}",
            }

        results = {
            "total_projects": 0,
            "total_proposals_tested": 0,
            "total_passed": 0,
            "total_failed": 0,
            "project_results": {},
        }

        # Find all project directories (subdirectories of projects_dir)
        project_dirs = [
            d for d in projects_path.iterdir() if d.is_dir() and not d.name.startswith(".")
        ]

        if verbose:
            print(f"\n=== Testing {len(project_dirs)} Cross-File Project(s) ===\n")

        for i, project_dir in enumerate(sorted(project_dirs), 1):
            results["total_projects"] += 1

            if verbose:
                print(f"[{i}/{len(project_dirs)}] Testing {project_dir.name}...")

            passed, failed, errors = self.test_project(str(project_dir), verbose=verbose)

            results["total_proposals_tested"] += passed + failed
            results["total_passed"] += passed
            results["total_failed"] += failed

            results["project_results"][project_dir.name] = {
                "passed": passed,
                "failed": failed,
                "errors": errors,
            }

            if verbose:
                total = passed + failed
                if total > 0:
                    print(f"  Result: {passed}/{total} passed\n")
                else:
                    print("  Result: No proposals\n")

        return results
