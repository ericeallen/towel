#!/usr/bin/env python3
"""Test case - what if pyproject.toml is missing or doesn't specify package-dir?"""

import tempfile
import os
from pathlib import Path
from towel.unification.project_layout import ProjectLayout

# Simulate structure WITHOUT package-dir in pyproject.toml
with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir) / "monte_chess"
    root.mkdir()

    # Create pyproject.toml WITHOUT package-dir
    (root / "pyproject.toml").write_text("""[project]
name = "monte-chess"
""")

    # Create src/monte_chess package structure
    src_dir = root / "src"
    src_dir.mkdir()

    monte_pkg = src_dir / "monte_chess"
    monte_pkg.mkdir()
    (monte_pkg / "__init__.py").write_text("")

    ai_dir = monte_pkg / "ai"
    ai_dir.mkdir()
    (ai_dir / "__init__.py").write_text("")
    (ai_dir / "mcts.py").write_text("def extracted_func(): pass")

    core_dir = monte_pkg / "core"
    core_dir.mkdir()
    (core_dir / "__init__.py").write_text("")
    (core_dir / "move_encoder.py").write_text("# needs import")

    # Change to project root
    os.chdir(root)
    print(f"Working directory: {os.getcwd()}")
    print()

    # User ran "towel dry src src_dry"
    from_path_str = "src/monte_chess/ai/mcts.py"
    to_path_str = "src/monte_chess/core/move_encoder.py"

    print(f"Relative paths (as stored in proposals):")
    print(f"  from_path: {from_path_str}")
    print(f"  to_path: {to_path_str}")

    # What refactor_engine.py does
    from_path = Path(from_path_str)
    to_path = Path(to_path_str)

    common_dir = Path(os.path.commonpath([str(from_path), str(to_path)]))
    print(f"\ncommon_dir: {common_dir}")
    print(f"common_dir absolute: {common_dir.resolve()}")

    layout = ProjectLayout.discover(common_dir)

    print(f"\nProjectLayout (NO package-dir in pyproject.toml):")
    print(f"  project_root: {layout.project_root}")
    print(f"  source_roots: {layout.source_roots}")

    abs_mod = layout.module_name_for(from_path)
    print(f"\nmodule_name_for({from_path}): {abs_mod}")
    print(f"Expected: monte_chess.ai.mcts")
    print(f"Got: {abs_mod}")
    assert abs_mod == "monte_chess.ai.mcts", abs_mod
