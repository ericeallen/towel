#!/usr/bin/env python3
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
Automate promotion of DRY `__extracted_func_*` helpers into human-friendly helpers.

This script helps rename the auto-generated `__extracted_func_*` functions created
by Towel's DRY engine into more meaningful, readable names. It analyzes the helper
functions' docstrings and context to generate appropriate names, then renames all
references throughout the codebase.

Usage:
    # List all helpers found in DRY output
    just promote-helpers --inventory

    # Preview what would be renamed (dry run)
    just promote-helpers --dry-run

    # Apply renaming to specific module
    just promote-helpers --module unification/refactor_engine.py

    # Apply renaming to specific helper
    just promote-helpers --helper __extracted_func_7

    # Apply renaming to all helpers
    just promote-helpers

The script maintains a JSON mapping file to track manual overrides and ensure
consistent naming across runs.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = REPO_ROOT / "tmp_out_dry_src" / "towel"
DEFAULT_TARGET_ROOT = REPO_ROOT / "src" / "towel"
DEFAULT_MAP_PATH = Path(__file__).with_name("promote_dry_helpers_map.json")
HELPER_PATTERN = re.compile(r"__extracted_func(?:_\d+)?$")


@dataclass
class HelperInfo:
    """Metadata about a helper definition discovered in the DRY output tree."""

    original_name: str
    container: Tuple[str, ...]
    docstring: Optional[str]
    lineno: int
    source: str


@dataclass
class HelperPlan:
    """Work item describing how to rename and insert a helper into the source tree."""

    info: HelperInfo
    target_name: str


@dataclass
class ModuleResult:
    """Summary of work performed (or planned via dry-run) for a module."""

    rel_path: Path
    renamed: List[Tuple[str, str, int]]
    inserted_helpers: int
    mapping_updates: Dict[str, str]
    changed: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--module",
        action="append",
        dest="modules",
        help="Limit processing to module paths relative to towel/, e.g. unification/refactor_engine.py",
    )
    parser.add_argument(
        "--helper",
        action="append",
        dest="helpers",
        help="Limit processing to specific helper names (e.g. __extracted_func_7).",
    )
    parser.add_argument(
        "--map",
        default=str(DEFAULT_MAP_PATH),
        help="Path to JSON mapping file storing manual helper rename overrides.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing files."
    )
    parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Root directory containing __extracted helpers (default: tmp_out_dry_src/towel)",
    )
    parser.add_argument(
        "--target-root",
        default=str(DEFAULT_TARGET_ROOT),
        help="Root directory whose modules should receive renamed helpers (default: src/towel)",
    )
    parser.add_argument(
        "--inventory",
        action="store_true",
        help="List helper definitions under --source-root without modifying target files.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reducer logging output (only final summary).",
    )
    return parser.parse_args()


def load_mapping(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse mapping file {path}: {exc}")


def save_mapping(path: Path, mapping: Dict[str, Dict[str, str]]) -> None:
    path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")


def iter_module_paths(
    source_root: Path,
    target_root: Path,
    module_filters: Optional[Sequence[str]],
) -> Iterable[Tuple[Path, Path, Path]]:
    if not source_root.exists():
        return
    for dry_path in sorted(source_root.rglob("*.py")):
        rel_path = dry_path.relative_to(source_root)
        target_path = target_root / rel_path
        if module_filters and not any(
            filter_token in str(rel_path) for filter_token in module_filters
        ):
            continue
        yield rel_path, dry_path, target_path


def run_inventory_mode(
    args: argparse.Namespace,
    source_root: Path,
    target_root: Path,
) -> None:
    total_helpers = 0
    modules_with_helpers = 0
    for rel_path, dry_path, _src_path in iter_module_paths(source_root, target_root, args.modules):
        helpers = collect_helper_infos(dry_path, args.helpers)
        if not helpers:
            continue
        modules_with_helpers += 1
        total_helpers += len(helpers)
        if args.quiet:
            continue
        print(f"{rel_path.as_posix()}: {len(helpers)} helper(s)")
        for helper in helpers:
            container = ".".join(helper.container) if helper.container else "<module>"
            doc_hint = helper.docstring.splitlines()[0] if helper.docstring else ""
            doc_suffix = f"  # {doc_hint}" if doc_hint else ""
            print(f"  - {helper.original_name} @ {container} (line {helper.lineno}){doc_suffix}")

    if modules_with_helpers == 0:
        print(f"No helper definitions found in {source_root} (filters applied).")
    else:
        summary = f"Inventory: {total_helpers} helper definitions across {modules_with_helpers} module(s)."
        print(summary)


def _module_name_variants(rel_path: Path) -> List[str]:
    no_ext = rel_path.with_suffix("")
    dotted = ".".join(no_ext.parts)
    simple = no_ext.name
    variants = {simple, dotted, f"towel.{dotted}"}
    if dotted.startswith("towel."):
        variants.add(dotted[len("towel.") :])
    if "." in dotted:
        variants.add(dotted.split(".", 1)[1])
    if simple:
        variants.add(f".{simple}")
    return [v for v in variants if v]


def rewrite_cross_module_aliases(
    mapping: Dict[str, Dict[str, str]],
    args: argparse.Namespace,
    target_root: Path,
    module_filters: Optional[Sequence[str]],
) -> List[ModuleResult]:
    if not mapping:
        return []
    alias_lookup: Dict[str, Dict[str, str]] = {}
    for rel_key, renames in mapping.items():
        rel_path = Path(rel_key)
        for variant in _module_name_variants(rel_path):
            alias_lookup.setdefault(variant, {}).update(renames)

    if not alias_lookup:
        return []

    extra_results: List[ModuleResult] = []

    for target_path in sorted(target_root.rglob("*.py")):
        rel_path = target_path.relative_to(target_root)
        if module_filters and not any(
            filter_token in str(rel_path) for filter_token in module_filters
        ):
            continue
        text = target_path.read_text()
        sanitized = _sanitize_for_parsing(text, drop_helper_imports=False)
        try:
            tree = ast.parse(sanitized)
        except SyntaxError:
            continue
        rename_pairs: Dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module_name = node.module or ""
            alias_map = alias_lookup.get(module_name)
            if not alias_map and module_name:
                alias_map = alias_lookup.get(f"towel.{module_name}")
            if not alias_map:
                continue
            for alias in node.names:
                old_name = alias.name
                if old_name in alias_map:
                    rename_pairs[old_name] = alias_map[old_name]
        if not rename_pairs:
            continue
        updated_text, rename_stats = apply_renames(text, rename_pairs.items())
        if updated_text == text:
            continue
        if not args.dry_run:
            target_path.write_text(updated_text)
        extra_results.append(
            ModuleResult(
                rel_path=rel_path,
                renamed=rename_stats,
                inserted_helpers=0,
                mapping_updates={},
                changed=True,
            )
        )

    return extra_results


def _sanitize_for_parsing(text: str, *, drop_helper_imports: bool = True) -> str:
    """Strip problematic helper import lines so ast.parse can succeed."""

    sanitized_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if drop_helper_imports and stripped.startswith("from ") and "__extracted_func" in stripped:
            # Preserve line count by emitting a bare newline (maintains indentation depth in blocks)
            sanitized_lines.append("\n" if line.endswith("\n") else "")
            continue
        sanitized_lines.append(line)
    return "".join(sanitized_lines)


def collect_helper_infos(
    dry_path: Path, helper_filters: Optional[Sequence[str]]
) -> List[HelperInfo]:
    text = dry_path.read_text()
    if "__extracted_func" not in text:
        return []
    parse_text = _sanitize_for_parsing(text)
    tree = ast.parse(parse_text)
    helpers: List[HelperInfo] = []

    filter_set = set(helper_filters or [])

    def visit(node: ast.AST, class_stack: Tuple[str, ...]) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
                visit(child, class_stack + (child.name,))
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if HELPER_PATTERN.fullmatch(child.name):
                    if filter_set and child.name not in filter_set:
                        # Skip helpers outside requested filter set
                        pass
                    else:
                        source = ast.get_source_segment(parse_text, child) or _slice_source(
                            parse_text, child
                        )
                        helpers.append(
                            HelperInfo(
                                original_name=child.name,
                                container=class_stack,
                                docstring=ast.get_docstring(child),
                                lineno=child.lineno,
                                source=source,
                            )
                        )
                # Continue traversing in case helper functions contain nested helpers
                visit(child, class_stack)

    visit(tree, tuple())
    helpers.sort(key=lambda info: info.lineno)
    return helpers


def _slice_source(text: str, node: ast.AST) -> str:
    lines = text.splitlines()
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    snippet = "\n".join(lines[start:end])
    return snippet + "\n"


def build_name_registry(tree: ast.AST) -> Dict[Tuple[str, ...], set]:
    registry: Dict[Tuple[str, ...], set] = {}

    def ensure_key(container: Tuple[str, ...]) -> set:
        if container not in registry:
            registry[container] = set()
        return registry[container]

    def visit(node: ast.AST, class_stack: Tuple[str, ...]) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
                visit(child, class_stack + (child.name,))
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ensure_key(class_stack).add(child.name)
                visit(child, class_stack)

    visit(tree, tuple())
    return registry


def locate_container(tree: ast.AST, container: Tuple[str, ...]) -> Optional[ast.AST]:
    current: ast.AST = tree
    for name in container:
        for child in getattr(current, "body", []):
            if isinstance(child, ast.ClassDef) and child.name == name:
                current = child
                break
        else:
            return None
    return current


def generate_helper_name(
    helper: HelperInfo,
    module_rel: Path,
    container_names: Dict[Tuple[str, ...], set],
) -> str:
    module_slug = module_rel.stem
    doc = (helper.docstring or "").strip()
    candidate_parts: List[str] = []
    if doc:
        first_line = doc.splitlines()[0]
        first_sentence = first_line.split(".")[0]
        candidate_parts = re.findall(r"[a-zA-Z0-9]+", first_sentence.lower())
    if not candidate_parts:
        suffix = helper.original_name.split("_")[-1].lstrip("_")
        candidate_parts = [module_slug, "helper", suffix]
    candidate = "_" + "_".join(candidate_parts[:5])
    taken = container_names.setdefault(helper.container, set())
    unique = candidate
    counter = 2
    while unique in taken:
        unique = f"{candidate}_{counter}"
        counter += 1
    taken.add(unique)
    return unique


def apply_renames(
    text: str, renames: Sequence[Tuple[str, str]]
) -> Tuple[str, List[Tuple[str, str, int]]]:
    result = text
    stats: List[Tuple[str, str, int]] = []
    for original, target in renames:
        pattern = re.compile(rf"\b{re.escape(original)}\b")
        result, count = pattern.subn(target, result)
        stats.append((original, target, count))
    return result, stats


def container_has_definition(tree: ast.AST, container: Tuple[str, ...], name: str) -> bool:
    target = locate_container(tree, container)
    if target is None:
        return False
    for child in getattr(target, "body", []):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
            return True
    return False


def anchor_line_for_container(tree: ast.AST, container: Tuple[str, ...]) -> Optional[int]:
    target = locate_container(tree, container)
    if target is None:
        return None
    body = getattr(target, "body", [])
    if not body:
        return getattr(target, "lineno", 1)
    last_stmt = body[-1]
    return getattr(last_stmt, "end_lineno", getattr(last_stmt, "lineno", 1))


def rewrite_helper_source(
    helper: HelperPlan,
    rename_map: Sequence[Tuple[str, str]],
) -> str:
    snippet = helper.info.source.rstrip()
    # Normalize line endings and ensure a trailing newline for clean insertion
    snippet = snippet.replace("\r\n", "\n")
    for original, target in rename_map:
        snippet = re.sub(rf"\b{re.escape(original)}\b", target, snippet)
    if helper.info.container:
        prefix = "\n"
    else:
        prefix = "\n\n"
    return f"{prefix}{snippet}\n"


def apply_insertions(
    text: str,
    insertions: List[Tuple[int, int, str]],
) -> str:
    if not insertions:
        return text
    lines = text.splitlines(keepends=True)
    insertions.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for anchor_idx, _lineno, snippet in insertions:
        snippet_lines = snippet.splitlines(keepends=True)
        if not snippet_lines:
            continue
        lines[anchor_idx:anchor_idx] = snippet_lines
    return "".join(lines)


def process_module(
    rel_path: Path,
    dry_path: Path,
    src_path: Path,
    mapping: Dict[str, Dict[str, str]],
    args: argparse.Namespace,
) -> Optional[ModuleResult]:
    if not src_path.exists():
        return None
    helper_infos = collect_helper_infos(dry_path, args.helpers)
    if not helper_infos:
        return None

    target_path = src_path
    target_text = target_path.read_text()
    target_tree = ast.parse(_sanitize_for_parsing(target_text))
    name_registry = build_name_registry(target_tree)
    rel_key = rel_path.as_posix()
    module_overrides = mapping.get(rel_key, {})

    helper_plans: List[HelperPlan] = []
    rename_sequence: List[Tuple[str, str]] = []
    mapping_updates: Dict[str, str] = {}

    for info in helper_infos:
        if not re.search(rf"\b{re.escape(info.original_name)}\b", target_text):
            continue
        if info.container and locate_container(target_tree, info.container) is None:
            continue
        target_name = module_overrides.get(info.original_name)
        if not target_name:
            target_name = generate_helper_name(info, rel_path, name_registry)
            mapping_updates[info.original_name] = target_name
        else:
            name_registry.setdefault(info.container, set()).add(target_name)
        helper_plans.append(HelperPlan(info=info, target_name=target_name))
        rename_sequence.append((info.original_name, target_name))

    if not helper_plans:
        return None

    updated_text, rename_stats = apply_renames(target_text, rename_sequence)
    tree_after = ast.parse(_sanitize_for_parsing(updated_text))

    insertions: List[Tuple[int, int, str]] = []
    lines = updated_text.splitlines(keepends=True)
    total_lines = len(lines)

    for plan in helper_plans:
        if container_has_definition(tree_after, plan.info.container, plan.target_name):
            continue
        anchor_line = anchor_line_for_container(tree_after, plan.info.container)
        if plan.info.container and anchor_line is None:
            continue
        if anchor_line is None:
            anchor_idx = total_lines
        else:
            anchor_idx = min(anchor_line, len(lines))
        snippet = rewrite_helper_source(plan, rename_sequence)
        insertions.append((anchor_idx, plan.info.lineno, snippet))

    final_text = apply_insertions(updated_text, insertions)
    changed = final_text != target_text

    if not args.dry_run and changed:
        target_path.write_text(final_text)

    return ModuleResult(
        rel_path=rel_path,
        renamed=rename_stats,
        inserted_helpers=len(insertions),
        mapping_updates=mapping_updates,
        changed=changed,
    )


def main() -> None:
    args = parse_args()
    source_root = Path(args.source_root).expanduser().resolve()
    target_root = Path(args.target_root).expanduser().resolve()

    if not source_root.exists():
        raise SystemExit(f"Source root does not exist: {source_root}")
    if not target_root.exists():
        raise SystemExit(f"Target root does not exist: {target_root}")

    if args.inventory:
        run_inventory_mode(args, source_root, target_root)
        return
    mapping_path = Path(args.map)
    mapping = load_mapping(mapping_path)

    results: List[ModuleResult] = []
    for rel_path, dry_path, src_path in iter_module_paths(source_root, target_root, args.modules):
        result = process_module(rel_path, dry_path, src_path, mapping, args)
        if result:
            results.append(result)

    mapping_changed = False
    for result in results:
        if result.mapping_updates:
            module_map = mapping.setdefault(result.rel_path.as_posix(), {})
            for helper_name, target in result.mapping_updates.items():
                if module_map.get(helper_name) != target:
                    module_map[helper_name] = target
                    mapping_changed = True

    if mapping_changed and not args.dry_run:
        save_mapping(mapping_path, mapping)
        if not args.quiet:
            print(f"Updated mapping file: {mapping_path}")

    cross_results = rewrite_cross_module_aliases(mapping, args, target_root, args.modules)
    results.extend(cross_results)

    if not results:
        if not args.quiet:
            print("No helpers matched the provided filters.")
        return

    for result in results:
        prefix = "[DRY]" if args.dry_run else ("[CHANGED]" if result.changed else "[SKIPPED]")
        if args.quiet and prefix == "[SKIPPED]":
            continue
        rename_desc = ", ".join(f"{orig}->{new} ({count})" for orig, new, count in result.renamed)
        if not rename_desc:
            rename_desc = "no references"
        print(
            f"{prefix} {result.rel_path.as_posix()}: {rename_desc}; "
            f"inserted {result.inserted_helpers} helpers"
        )

    changed_modules = sum(1 for r in results if r.changed)

    if not args.quiet:
        summary = (
            f"Processed {len(results)} module(s); "
            f"applied changes to {changed_modules}; "
            f"dry-run={'yes' if args.dry_run else 'no'}"
        )
        print(summary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
