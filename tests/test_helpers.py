"""
Test helpers for Towel tests.

Provides utilities for safe testing that never pollutes test_examples
or the working directory.
"""

import tempfile
import shutil
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Generator


@contextmanager
def temporary_test_directory() -> Generator[Path, None, None]:
    """
    Context manager that creates a temporary directory for test output.

    The directory is automatically cleaned up when exiting the context,
    even if an exception occurs.

    Usage:
        with temporary_test_directory() as temp_dir:
            # temp_dir is a pathlib.Path to a clean temporary directory
            output_file = temp_dir / "output.py"
            # ... do test operations ...
            # temp_dir is automatically deleted on exit

    Yields:
        Path: Temporary directory path that will be cleaned up automatically
    """
    temp_dir = tempfile.mkdtemp(prefix="towel_test_")
    temp_path = Path(temp_dir)
    try:
        yield temp_path
    finally:
        # Clean up the temporary directory
        if temp_path.exists():
            shutil.rmtree(temp_path)


@contextmanager
def temporary_copy_of_examples() -> Generator[Path, None, None]:
    """
    Context manager that creates a temporary copy of test_examples.

    Useful for tests that need to apply refactorings and check the results
    without modifying the original test examples.

    Usage:
        with temporary_copy_of_examples() as temp_examples:
            # temp_examples is a Path to a copy of test_examples
            engine.apply_refactoring(temp_examples / "example1_simple.py", proposal)
            # Original test_examples unchanged
            # temp_examples automatically deleted on exit

    Yields:
        Path: Temporary directory containing a copy of all test examples
    """
    with temporary_test_directory() as temp_dir:
        examples_copy = temp_dir / "test_examples"
        shutil.copytree("test_examples", examples_copy)
        yield examples_copy


def copy_example_to_temp(example_name: str, temp_dir: Path) -> Path:
    """
    Copy a specific test example to a temporary directory.

    Args:
        example_name: Name of the example file (e.g., "example1_simple.py")
        temp_dir: Temporary directory to copy to (from temporary_test_directory())

    Returns:
        Path to the copied file in temp_dir

    Example:
        with temporary_test_directory() as temp_dir:
            example = copy_example_to_temp("example1_simple.py", temp_dir)
            # Work with example file in temp_dir
    """
    source = Path("test_examples") / example_name
    if not source.exists():
        raise FileNotFoundError(f"Test example not found: {source}")

    dest = temp_dir / example_name
    shutil.copy2(source, dest)
    return dest


def get_test_example_path(example_name: str) -> Path:
    """
    Get the path to a test example for reading only.

    Use this when you only need to read from test examples, not write to them.

    Args:
        example_name: Name of the example file (e.g., "example1_simple.py")

    Returns:
        Path to the test example (read-only - don't write to this!)

    Example:
        example_path = get_test_example_path("example1_simple.py")
        proposals = engine.analyze_file(str(example_path))
    """
    path = Path("test_examples") / example_name
    if not path.exists():
        raise FileNotFoundError(f"Test example not found: {path}")
    return path


@contextmanager
def temporary_output_directory() -> Generator[Path, None, None]:
    """
    Context manager for temporary output directory with better naming.

    Like temporary_test_directory but with a more specific name for output.

    Usage:
        with temporary_output_directory() as output_dir:
            engine.apply_refactoring_multi_file(proposal, output_dir)
            # Check outputs in output_dir
            # Automatically cleaned up on exit

    Yields:
        Path: Temporary output directory
    """
    with temporary_test_directory() as temp_dir:
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        yield output_dir


def assert_file_not_modified(file_path: Path, original_content: str) -> None:
    """
    Assert that a file has not been modified.

    Useful for ensuring test_examples remain pristine after tests.

    Args:
        file_path: Path to file to check
        original_content: Expected original content

    Raises:
        AssertionError: If file has been modified

    Example:
        original = example_path.read_text()
        # ... run tests ...
        assert_file_not_modified(example_path, original)
    """
    current_content = file_path.read_text()
    if current_content != original_content:
        raise AssertionError(
            f"File {file_path} was modified during test! "
            f"Original {len(original_content)} bytes, "
            f"now {len(current_content)} bytes"
        )
