"""
Test helpers for Towel tests.

Provides utilities for safe testing that never pollutes test_examples
or the working directory.
"""

import ast
import contextlib
import io
import shutil
import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator, List, Sequence, TypedDict, Unpack

from towel.unification.refactor_engine import UnificationRefactorEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = PROJECT_ROOT / "test_examples"


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


# --- Shared parsing and engine-running helpers -----------------------------
#
# Each of these was copied into several test modules; they live here so a
# change to how a test parses a block or runs the engine is made once.


def parse_block(source: str) -> List[ast.stmt]:
    """Statements of ``source`` after dedenting, so tests can write indented blocks inline."""
    return ast.parse(textwrap.dedent(source)).body


def fix_locations(nodes: Sequence[ast.stmt]) -> List[ast.stmt]:
    """Give synthesized statements the positions ``ast.unparse`` and ``compile`` require."""
    module = ast.Module(body=list(nodes), type_ignores=[])
    ast.fix_missing_locations(module)
    return module.body


def function_def(source: str) -> ast.FunctionDef:
    """The function that ``source`` starts with, dedented first."""
    node = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(node, ast.FunctionDef), ast.dump(node)
    return node


def module_functions(source: str) -> dict[str, ast.FunctionDef]:
    """Module-level function definitions of ``source`` by name."""
    return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


def unparsed_body(function: ast.FunctionDef) -> str:
    """The body of ``function`` rendered one statement per line, for comparing shapes."""
    return "\n".join(ast.unparse(statement) for statement in function.body)


def example_paths(names: List[str]) -> List[str]:
    """Absolute paths of the named files under ``test_examples``."""
    return [str(EXAMPLES_DIR / name) for name in names]


def write_module(directory: Path, code: str, name: str = "m.py") -> str:
    """Write dedented ``code`` as ``directory/name`` and return its path as a string."""
    path = directory / name
    path.write_text(textwrap.dedent(code))
    return str(path)


def write_file(path: Path, content: str) -> None:
    """Write ``content`` at ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class EngineOptions(TypedDict, total=False):
    """Keyword options tests forward to ``UnificationRefactorEngine``."""

    parameterize_constants: bool
    promote_equal_hof_literals: bool
    skip_trivial_helpers: bool
    reuse_existing_functions: bool
    annotate_helpers: bool
    snippet_formatter: Callable[[str], str]


def refactor_to_fixed_point_silently(
    path: str, min_lines: int = 1, **engine_options: Unpack[EngineOptions]
) -> tuple[str, int]:
    """Refactor one file to a fixed point with the engine's progress output discarded.

    Returns the final source and how many refactorings were applied.
    """
    engine = UnificationRefactorEngine(min_lines=min_lines, **engine_options)
    with contextlib.redirect_stdout(io.StringIO()):
        final, applied, _descriptions = engine.refactor_to_fixed_point(path, max_iterations=0)
    return final, applied
