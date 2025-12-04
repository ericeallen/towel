#!/usr/bin/env python3
"""
Command-line interface for Towel.

This module provides CLI entry points for the Towel code refactoring tool.
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path
from typing import List, Tuple, Optional


def main() -> None:
    """Main entry point with subcommands."""
    parser = argparse.ArgumentParser(
        prog="code-towel",
        description="A Python tool that DRYs your code - finds and refactors repeated code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  towel preview src/                    # Preview duplicates (read-only)
  towel dry src/ cleaned/               # Apply refactorings to new directory
  towel dry src/ src/                   # Apply refactorings in-place
  towel rename-helpers                  # Rename extracted functions with LLM assistance

For more help on a specific command:
  towel <command> --help
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="towel 1.0.2",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add subcommand parsers
    _add_dry_parser(subparsers)
    _add_preview_parser(subparsers)
    _add_rename_helpers_parser(subparsers)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch to appropriate handler
    if args.command == "dry":
        _run_dry(args)
    elif args.command == "preview":
        _run_preview(args)
    elif args.command == "rename-helpers":
        _run_rename_helpers(args)


def _add_dry_parser(subparsers) -> None:
    """Add 'dry' subcommand parser."""
    parser = subparsers.add_parser(
        "dry",
        help="Apply refactorings to remove duplicate code",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Analyze and apply unification-based refactorings to file(s) or directories.",
        epilog="""
Examples:
  towel dry src/ cleaned/                         # Refactor directory until fixed point
  towel dry file.py file_out.py --non-interactive # Non-interactive single file
  towel dry src/ out/ --max-iterations 50         # Cap at 50 applied refactorings
  towel dry src/ out/ --progress detail           # Verbose per-phase output
        """,
    )

    parser.add_argument("input", help="Input file or directory")
    parser.add_argument("output", help="Output file or directory (can be same as input)")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without interactive confirmation (skip prompt)",
    )

    pref_group = parser.add_mutually_exclusive_group()
    pref_group.add_argument(
        "--prefer-absolute-imports",
        dest="prefer_absolute_imports",
        action="store_true",
        help="Prefer absolute imports for cross-file extractions when possible",
    )
    pref_group.add_argument(
        "--no-prefer-absolute-imports",
        dest="prefer_absolute_imports",
        action="store_false",
        help="Prefer local/same-dir imports when possible",
    )
    parser.set_defaults(prefer_absolute_imports=None)

    pep_group = parser.add_mutually_exclusive_group()
    pep_group.add_argument(
        "--pep420",
        dest="pep420",
        action="store_true",
        help="Treat directories as namespace packages (PEP 420) when deriving module paths",
    )
    pep_group.add_argument(
        "--no-pep420",
        dest="pep420",
        action="store_false",
        help="Require __init__.py for packages when deriving module paths",
    )
    parser.set_defaults(pep420=None)

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Maximum refactorings to apply (0 = unlimited until fixed point, default: 0)",
    )

    parser.add_argument(
        "--progress",
        choices=["auto", "tqdm", "none", "detail"],
        default="tqdm",
        help="Progress display mode: 'tqdm' shows bars, 'auto' falls back if tqdm unavailable, "
        "'none' disables output, 'detail' prints per-phase summaries.",
    )


def _add_preview_parser(subparsers) -> None:
    """Add 'preview' subcommand parser."""
    parser = subparsers.add_parser(
        "preview",
        help="Preview duplicate code detection (read-only)",
        description="Preview unification-based refactoring opportunities without modifying files.",
    )

    parser.add_argument("target", help="File or directory to analyze")

    pref_group = parser.add_mutually_exclusive_group()
    pref_group.add_argument(
        "--prefer-absolute-imports",
        dest="prefer_absolute_imports",
        action="store_true",
        help="Prefer absolute imports for cross-file extractions when possible",
    )
    pref_group.add_argument(
        "--no-prefer-absolute-imports",
        dest="prefer_absolute_imports",
        action="store_false",
        help="Prefer local/same-dir imports when possible",
    )
    parser.set_defaults(prefer_absolute_imports=None)

    pep_group = parser.add_mutually_exclusive_group()
    pep_group.add_argument(
        "--pep420",
        dest="pep420",
        action="store_true",
        help="Treat directories as namespace packages (PEP 420) when deriving module paths",
    )
    pep_group.add_argument(
        "--no-pep420",
        dest="pep420",
        action="store_false",
        help="Require __init__.py for packages when deriving module paths",
    )
    parser.set_defaults(pep420=None)


def _add_rename_helpers_parser(subparsers) -> None:
    """Add 'rename-helpers' subcommand parser."""
    parser = subparsers.add_parser(
        "rename-helpers",
        help="Rename extracted functions using LLM assistance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Rename auto-generated __extracted_func_* functions into meaningful names.

This tool analyzes the extracted functions and uses an LLM to suggest better names
based on the code's purpose and context. You can use Claude Code, ChatGPT, or any
other AI coding assistant to generate the names.

The tool works in two modes:

1. INTERACTIVE MODE (default):
   Generates a prompt for an LLM to review your code and suggest new names.
   You paste the LLM's response back, and the tool applies the renamings.

2. FILE MODE (--rename-file):
   Provide a JSON file with old_name -> new_name mappings.
   The tool applies these renamings across your entire codebase.
        """,
        epilog="""
Examples:
  # Interactive mode: generate LLM prompt and apply suggestions
  towel rename-helpers src/

  # List all extracted helpers
  towel rename-helpers src/ --list

  # Apply renamings from a JSON file
  towel rename-helpers src/ --rename-file renames.json

  # Dry run (preview only)
  towel rename-helpers src/ --dry-run

  # Specify specific files or functions
  towel rename-helpers src/ --file mymodule.py
  towel rename-helpers src/ --function __extracted_func_7
        """,
    )

    parser.add_argument(
        "target",
        help="Directory containing code with __extracted_func_* functions",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List all extracted helper functions found",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files",
    )

    parser.add_argument(
        "--rename-file",
        type=Path,
        help="JSON file with old_name -> new_name mappings to apply",
    )

    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        help="Limit processing to specific file(s) (relative path)",
    )

    parser.add_argument(
        "--function",
        action="append",
        dest="functions",
        help="Limit processing to specific function(s) by name",
    )

    parser.add_argument(
        "--llm",
        choices=["claude", "gpt", "copilot", "generic"],
        default="generic",
        help="Format the LLM prompt for a specific AI assistant (default: generic)",
    )


def _run_dry(args: argparse.Namespace) -> None:
    """Run the dry command."""
    # Import here to avoid loading heavy modules if not needed
    import os
    import shutil
    from towel.unification.refactor_engine import UnificationRefactorEngine

    input_path = args.input
    output_path = args.output

    # Check if input exists
    if not os.path.exists(input_path):
        print(f"Error: '{input_path}' does not exist")
        sys.exit(1)

    # Determine if it's a file or directory
    is_file = os.path.isfile(input_path)
    is_dir = os.path.isdir(input_path)

    if not is_file and not is_dir:
        print(f"Error: '{input_path}' is neither a file nor a directory")
        sys.exit(1)

    # Check input file extension for single files
    if is_file and not input_path.endswith(".py"):
        print(f"Warning: '{input_path}' is not a Python file (.py)")
        response = input("Analyze anyway? (y/N): ").strip().lower()
        if response != "y":
            return

    # Copy input to output
    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_path)

    if abs_input == abs_output:
        print(f"Modifying in place: {input_path}")
    else:
        print(f"Copying {input_path} -> {output_path}")
        if is_file:
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            shutil.copy2(input_path, output_path)
        else:
            if os.path.exists(output_path):
                shutil.rmtree(output_path)
            shutil.copytree(input_path, output_path)
    print()

    # Create engine
    engine = UnificationRefactorEngine(
        max_parameters=5,
        min_lines=3,
        parameterize_constants=True,
        prefer_absolute_imports=args.prefer_absolute_imports,
        pep420_namespace_packages=args.pep420,
    )

    # Use fixed-point iteration
    print("=" * 70)
    print("APPLYING REFACTORINGS (FIXED-POINT ITERATION)")
    print("=" * 70)
    print()
    print("This will apply refactorings one at a time until no more are found.")
    print("Extracted functions will be placed at the end of files.")
    print()

    if not args.non_interactive:
        response = input("Proceed? (y/N): ").strip().lower()
        if response != "y":
            print("Aborted.")
            return

    print()

    # Apply refactorings to fixed point
    if is_file:
        print(f"Refactoring file: {output_path}")
        final_code, num_applied, descriptions = engine.refactor_to_fixed_point(
            output_path,
            max_iterations=args.max_iterations,
        )

        with open(output_path, "w") as f:
            f.write(final_code)

        if num_applied > 0:
            print(f"\n✓ Applied {num_applied} refactoring(s):")
            for i, desc in enumerate(descriptions, 1):
                print(f"  {i}. {desc}")
        else:
            print("\nNo refactorings found!")
    else:
        print(f"Refactoring directory: {output_path}")
        results, termination_reason = engine.refactor_directory_to_fixed_point(
            output_path,
            output_path,
            max_iterations=args.max_iterations,
            progress=args.progress,
        )

        if results:
            total_refactorings = sum(count for count, _ in results.values())
            print(f"\n✓ Applied {total_refactorings} refactoring(s) across {len(results)} file(s)")
            print(f"  Termination: {termination_reason}")
            for file_path, (count, descriptions) in sorted(results.items()):
                print(f"\n  {file_path}: {count} refactoring(s)")
                for desc in descriptions[:3]:
                    print(f"    - {desc}")
                if len(descriptions) > 3:
                    print(f"    ... and {len(descriptions) - 3} more")
        else:
            print("\nNo refactorings found! Termination: fixed_point")


def _run_preview(args: argparse.Namespace) -> None:
    """Run the preview command."""
    import os
    import ast
    from towel.unification.refactor_engine import (
        UnificationRefactorEngine,
        filter_overlapping_proposals,
    )

    target = args.target

    # Check if target exists
    if not os.path.exists(target):
        print(f"Error: '{target}' does not exist")
        sys.exit(1)

    is_file = os.path.isfile(target)
    is_dir = os.path.isdir(target)

    if not is_file and not is_dir:
        print(f"Error: '{target}' is neither a file nor a directory")
        sys.exit(1)

    # Create engine
    engine = UnificationRefactorEngine(
        max_parameters=5,
        min_lines=3,
        parameterize_constants=True,
        prefer_absolute_imports=args.prefer_absolute_imports,
        pep420_namespace_packages=args.pep420,
    )

    # Analyze
    if is_file:
        if not target.endswith(".py"):
            print(f"Note: '{target}' is not a Python file (.py)")
            print()

        print(f"Analyzing file: {target}")
        all_proposals = engine.analyze_file(target)
    else:
        print(f"Analyzing directory: {target}")
        all_proposals = engine.analyze_directory(
            target, recursive=True, verbose=True, progress="auto"
        )

    print(f"\nFound {len(all_proposals)} refactoring opportunities")

    if not all_proposals:
        print("No duplicates found!")
        return

    # Filter overlapping proposals
    proposals = filter_overlapping_proposals(all_proposals)

    if len(proposals) < len(all_proposals):
        print(f"Filtered to {len(proposals)} non-overlapping proposals")
        print(f"(Removed {len(all_proposals) - len(proposals)} overlapping proposals)")

    print("\n" + "=" * 70)
    print("REFACTORING OPPORTUNITIES")
    print("=" * 70)

    for i, prop in enumerate(proposals[:10], 1):
        print(f"\n{i}. {prop.description}")
        print(f"   Parameters: {prop.parameters_count}")

        # Show which files are affected
        files_affected = set()
        for replacement in prop.replacements:
            if replacement.file_path:
                files_affected.add(replacement.file_path)
            else:
                files_affected.add(prop.file_path)

        if len(files_affected) > 1:
            print(f"   Type: Cross-file ({len(files_affected)} files)")
            for f in sorted(files_affected):
                print(f"      - {f}")
        else:
            print(f"   Type: Same file ({prop.file_path})")

        # Show extracted function preview
        print("\n   Extracted function preview:")
        try:
            func_code = ast.unparse(prop.extracted_function)
            lines = func_code.split("\n")
            for line in lines[:8]:
                print(f"      {line}")
            if len(lines) > 8:
                print(f"      ... ({len(lines) - 8} more lines)")
        except ValueError as e:
            print(f"      (Preview unavailable: {e})")
            print(f"      Function name: {prop.extracted_function.name}")

    if len(proposals) > 10:
        print(f"\n... and {len(proposals) - 10} more proposals")

    print("\n" + "=" * 70)
    print("\nTo apply these refactorings, run:")
    if is_file:
        print(f"  towel dry {target} <output>")
    else:
        print(f"  towel dry {target} <output_dir>")
    print()


def _run_rename_helpers(args: argparse.Namespace) -> None:
    """Run the rename-helpers command."""
    target = Path(args.target)

    if not target.exists():
        print(f"Error: '{target}' does not exist")
        sys.exit(1)

    if not target.is_dir():
        print(f"Error: '{target}' must be a directory")
        sys.exit(1)

    # Find all extracted helper functions
    helpers = _find_extracted_helpers(target, args.files, args.functions)

    if not helpers:
        print(f"No __extracted_func_* functions found in {target}")
        return

    # List mode
    if args.list:
        print(f"\nFound {len(helpers)} extracted helper function(s):\n")
        for file_path, func_name, lineno, source_preview in helpers:
            rel_path = file_path.relative_to(target)
            print(f"  {rel_path}:{lineno}")
            print(f"    Function: {func_name}")
            if source_preview:
                lines = source_preview.split("\n")[:3]
                for line in lines:
                    print(f"      {line}")
                if len(source_preview.split("\n")) > 3:
                    print("      ...")
            print()
        return

    # Apply renamings from file
    if args.rename_file:
        _apply_rename_file(target, helpers, args.rename_file, args.dry_run)
        return

    # Interactive LLM mode
    _run_interactive_llm_mode(target, helpers, args.llm, args.dry_run)


def _find_extracted_helpers(
    target: Path,
    file_filters: Optional[List[str]],
    function_filters: Optional[List[str]],
) -> List[Tuple[Path, str, int, str]]:
    """Find all __extracted_func_* functions in target directory."""
    import ast
    import re

    helper_pattern = re.compile(r"^__extracted_func(?:_\d+)?$")
    helpers = []

    for py_file in target.rglob("*.py"):
        # Apply file filters
        if file_filters:
            rel_path = str(py_file.relative_to(target))
            if not any(f in rel_path for f in file_filters):
                continue

        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if helper_pattern.match(node.name):
                    # Apply function filters
                    if function_filters and node.name not in function_filters:
                        continue

                    # Get source preview
                    lines = source.split("\n")
                    if hasattr(node, "lineno") and node.lineno <= len(lines):
                        start = node.lineno - 1
                        end = min(start + 5, len(lines))
                        preview = "\n".join(lines[start:end])
                    else:
                        preview = ""

                    helpers.append((py_file, node.name, node.lineno, preview))

    return sorted(helpers, key=lambda x: (str(x[0]), x[2]))


def _apply_rename_file(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    rename_file: Path,
    dry_run: bool,
) -> None:
    """Apply renamings from a JSON file."""
    import json
    import re

    # Load rename mappings
    try:
        with open(rename_file) as f:
            renames = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error reading rename file: {e}")
        sys.exit(1)

    if not isinstance(renames, dict):
        print("Error: Rename file must contain a JSON object (dict)")
        sys.exit(1)

    # Apply renamings
    total_changes = 0

    for old_name, new_name in renames.items():
        if not isinstance(new_name, str) or not new_name:
            print(f"Warning: Skipping invalid mapping {old_name} -> {new_name}")
            continue

        # Validate new name
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", new_name):
            print(f"Warning: Skipping invalid Python identifier: {new_name}")
            continue

        # Find and replace in all Python files
        count = _rename_function_in_directory(target, old_name, new_name, dry_run)
        if count > 0:
            total_changes += count
            print(f"  {old_name} -> {new_name} ({count} replacement(s))")

    if dry_run:
        print(f"\n[DRY RUN] Would make {total_changes} change(s)")
    else:
        print(f"\n✓ Applied {total_changes} change(s)")


def _rename_function_in_directory(
    target: Path,
    old_name: str,
    new_name: str,
    dry_run: bool,
    file_filter: Optional[Path] = None,
) -> int:
    """Rename a function throughout all Python files in directory.

    Args:
        target: Directory to search in
        old_name: Current function name
        new_name: New function name
        dry_run: If True, don't actually modify files
        file_filter: If provided, only rename in this specific file
    """
    import re

    # Pattern to match function definitions and calls
    # This is a simple regex - could be improved with AST rewriting for accuracy
    patterns = [
        (rf"\bdef {re.escape(old_name)}\b", f"def {new_name}"),
        (rf"\b{re.escape(old_name)}\(", f"{new_name}("),
    ]

    total_replacements = 0

    for py_file in target.rglob("*.py"):
        # If file_filter is specified, only process that file
        if file_filter and py_file != file_filter:
            continue

        try:
            content = py_file.read_text()
            new_content = content

            for pattern, replacement in patterns:
                new_content = re.sub(pattern, replacement, new_content)

            if new_content != content:
                replacements = sum(len(re.findall(pattern, content)) for pattern, _ in patterns)
                total_replacements += replacements

                if not dry_run:
                    py_file.write_text(new_content)

        except (UnicodeDecodeError, PermissionError):
            continue

    return total_replacements


def _run_interactive_llm_mode(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    llm_type: str,
    dry_run: bool,
) -> None:
    """Run interactive mode: generate LLM prompt and apply suggestions."""
    print(f"\nFound {len(helpers)} extracted helper function(s) to rename.\n")  # noqa: F541
    print("=" * 70)
    print("STEP 1: LLM PROMPT GENERATION")
    print("=" * 70)
    print()

    # Generate LLM prompt
    prompt = _generate_llm_prompt(target, helpers, llm_type)

    print("Copy the following prompt and paste it into your LLM assistant:")
    print("\n" + "=" * 70)
    print(prompt)
    print("=" * 70 + "\n")

    print("After the LLM provides suggestions, paste the JSON response below.")
    print("The response should be a JSON object mapping old names to new names.")
    print("You can use file-qualified names (e.g., 'path/to/file.py:func_name') to")
    print("rename functions only in specific files, or just the function name to rename")
    print("across all files.")
    print()
    print("Example:")
    print('  {"__extracted_func_1": "calculate_total"}')
    print('  {"src/utils.py:__extracted_func_2": "validate_input"}')
    print()
    print(
        "Paste the JSON response below, then press ENTER followed by Ctrl+D (or Ctrl+Z on Windows):"
    )
    print()

    # Read LLM response from stdin
    try:
        llm_response = sys.stdin.read().strip()
    except KeyboardInterrupt:
        print("\nAborted.")
        return

    if not llm_response:
        print("No response provided. Aborted.")
        return

    # Extract JSON from response (handle markdown code blocks)
    import json
    import re

    # Try to extract JSON from markdown code block
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", llm_response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find JSON object directly
        json_match = re.search(r"\{.*\}", llm_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = llm_response

    # Parse JSON
    try:
        renames = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"\nError parsing JSON response: {e}")
        print("Please ensure the response is valid JSON.")
        return

    if not isinstance(renames, dict):
        print("Error: Response must be a JSON object (dict)")
        return

    print("\n" + "=" * 70)
    print("STEP 2: APPLYING RENAMINGS")
    print("=" * 70)
    print()

    # Apply renamings
    total_changes = 0
    for old_name_spec, new_name in renames.items():
        if not isinstance(new_name, str) or not new_name:
            print(f"Warning: Skipping invalid mapping {old_name_spec} -> {new_name}")
            continue

        # Parse file-qualified names (e.g., "path/to/file.py:function_name")
        file_filter = None
        if ":" in old_name_spec:
            file_path_str, old_name = old_name_spec.rsplit(":", 1)
            # Resolve file path relative to target
            file_filter = target / file_path_str
            if not file_filter.exists():
                print(f"Warning: File not found: {file_filter}")
                print(f"  Skipping: {old_name_spec} -> {new_name}")
                continue
        else:
            old_name = old_name_spec

        count = _rename_function_in_directory(target, old_name, new_name, dry_run, file_filter)
        if count > 0:
            total_changes += count
            if file_filter:
                print(f"  ✓ {old_name_spec} -> {new_name} ({count} replacement(s))")
            else:
                print(f"  ✓ {old_name} -> {new_name} ({count} replacement(s))")

    if dry_run:
        print(f"\n[DRY RUN] Would make {total_changes} change(s)")
    else:
        print(f"\n✓ Successfully applied {total_changes} change(s)")


def _generate_llm_prompt(
    target: Path,
    helpers: List[Tuple[Path, str, int, str]],
    llm_type: str,
) -> str:
    """Generate a prompt for an LLM to suggest better function names."""

    intro = """I have extracted duplicate code into helper functions, but they have generic names like __extracted_func_1, __extracted_func_2, etc.

Please review each function and suggest a better, more descriptive name based on what the function does. The new names should:
- Be descriptive and indicate the function's purpose
- Follow Python naming conventions (lowercase with underscores)
- Be concise but meaningful
- Avoid generic names like "helper" or "utility"

Here are the extracted functions:

"""

    functions_section = ""
    for i, (file_path, func_name, lineno, source_preview) in enumerate(helpers, 1):
        rel_path = file_path.relative_to(target)
        functions_section += f"\n{i}. {func_name} ({rel_path}:{lineno})\n"
        if source_preview:
            functions_section += "```python\n"
            functions_section += source_preview + "\n"
            functions_section += "```\n"

    output_format = """

Please provide your suggestions as a JSON object mapping old names to new names.

If the same function name appears in multiple files, use file-qualified names (path:function_name)
to rename them individually. Otherwise, you can use just the function name to rename across all files.

For example:

```json
{
  "src/utils.py:__extracted_func_1": "calculate_total_price",
  "src/validators.py:__extracted_func_1": "validate_email_format",
  "__extracted_func_2": "format_date_string"
}
```

Only include the JSON object in your response.
"""

    return intro + functions_section + output_format


# Legacy entry points for backwards compatibility
def dry_main() -> None:
    """Direct entry point for 'towel-dry' command (legacy)."""
    # Emulate 'towel dry' by injecting 'dry' as first argument
    sys.argv.insert(1, "dry")
    main()


def preview_main() -> None:
    """Direct entry point for 'towel-preview' command (legacy)."""
    # Emulate 'towel preview' by injecting 'preview' as first argument
    sys.argv.insert(1, "preview")
    main()


if __name__ == "__main__":
    main()
