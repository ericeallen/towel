# User Documentation vs. Shipped CLI Review (Gemini, 2026-10-31)

## Summary

We audited every CLI invocation, option flag, default value, API snippet, and sample output across `README.md`, `docs/QUICKSTART.md`, `docs/USAGE_GUIDE.md`, `CONTRIBUTING.md`, and `src/towel/cli.py` (`--help` text and implementation) at commit `99ab13e` (Towel 1.947), executing all offline `preview`, `dry`, and API examples against `test_examples/` and standalone fixtures.
We found **7 discrepancies** in total: **3 documentation inaccuracies** (fixed minimally in `docs/QUICKSTART.md` and `docs/USAGE_GUIDE.md`) and **4 CLI/source issues** in `src/towel/` (left for a source change per task scope).
All sample outputs in `README.md` and `docs/USAGE_GUIDE.md` (Examples 1–3) matched the shipped CLI output.

---

## Discrepancies Found

| # | Location (`file:line`) | What It Said | What the CLI / Source Does | Status |
|---|---|---|---|---|
| 1 | `src/towel/cli.py:94` (`towel --help` epilog) | `towel rename-helpers                  # Rename extracted functions with LLM assistance` | `towel rename-helpers` requires a positional `target` directory argument (`src/towel/cli.py:562-565`). Running `towel rename-helpers` without `target` exits with code 2: `towel rename-helpers: error: the following arguments are required: target`. (`towel rename-helpers --help` at `src/towel/cli.py:542` includes `path/to/project`.) | Left for a source change |
| 2 | `docs/QUICKSTART.md:48-51` | "Without it [i.e., `--no-interactive`], and with a type checker installed, it first reports what verification will involve on this project — how many files each candidate signature is checked against and how many third-party packages their imports pull in — and then asks before doing any of it." | `_verification_cost` (`src/towel/cli.py:779, 808-845`) only prints the verification-cost note when `options.types` is `True` and `input` is a directory containing at least 25 `*.py` files (`_COST_NOTE_FILES = 25`). Smaller directories and single-file targets skip the note and prompt `Proceed? (y/N): ` directly. | Fixed in `docs/QUICKSTART.md:48-52` |
| 3 | `src/towel/cli.py:808-845, 1101-1120` (`_verification_cost`) | Prints `Type checking is on (the installed checker), and this is where the time goes. Every candidate signature is verified against the whole project: <N> Python files here, importing <M> third-party packages...` before prompting `Proceed? (y/N): `. | `_verification_cost` runs before `_type_oracle` (`src/towel/cli.py:1120`) and never checks whether `mypy` or `pyright` is installed. On a directory with `>= 25` `.py` files in a stdlib-only installation (`pip install code-towel`), it states `Type checking is on (the installed checker)` before confirmation and then prints `Note: mypy is not installed, so helper annotations are copied from the call sites but not inferred or verified.` Furthermore, it enumerates `list(project_path.rglob("*.py"))` directly instead of `python_sources(project_path, excluded=options.exclude)`, counting `.venv` and `--exclude` files. | Left for a source change |
| 4 | `docs/USAGE_GUIDE.md:88-89` | "`tqdm`: Rich progress bar showing applied count, queue length and the proposal being weighed, redrawn on a heartbeat so a long verification does not look like a hung run." / "`auto`: Attempts `tqdm`, falls back to a textual single-line bar." | `tqdm` is not an install dependency of `code-towel` (`pyproject.toml:28`, only in `[dev]`). Under the default `tqdm` mode when `tqdm` is absent, `_note_missing_tqdm` (`src/towel/unification/fixed_point.py:106-114`) logs a warning once on stderr, shows inline bars during analysis/pairing/unification, and omits the apply bar; `auto` falls back to the inline apply bar without warning. | Fixed in `docs/USAGE_GUIDE.md:88-89` |
| 5 | `src/towel/unification/fixed_point.py:112` (`_note_missing_tqdm`) | Logs warning: `tqdm is not installed; the pairing phase shows an inline bar on stderr, the others none` | When `progress="tqdm"` (the default) and `tqdm` is not installed, `wants_bar("tqdm")` is `True` (`src/towel/unification/progress.py:54`), so `run_pipeline` (`src/towel/unification/pipeline.py:456-475`), `_PairingProgress` (`src/towel/unification/refactor_engine.py:270-298`), and `_evaluate_pairs_serial` (`src/towel/unification/parallel.py:310-324`) all print inline bars on stderr (`Analyzing files:`, `Pairing blocks:`, and `Analyzing pairs (unify):`). Only `_ApplyProgress.inline` (`src/towel/unification/fixed_point.py:1164`) suppresses its inline bar unless `mode == "auto"`. | Left for a source change |
| 6 | `src/towel/cli.py:375, 504` and `src/towel/unification/fixed_point.py:319` (`--progress detail` on single files) | `towel dry --help` says `'detail' logs each pass's discovered proposals and follow-ups instead of a bar`; `towel preview --help` says `'detail' logs each complete-preview pass; --quick performs only one analysis`. | In single-file runs (`refactor_to_fixed_point`, `src/towel/unification/fixed_point.py:319`), `analysis_progress = progress if wants_bar(progress) else "none"` maps `detail` to `"none"` without using `_ApplyProgress`, so `--progress detail` on a single file logs no `[towel]` detail lines (behaving identically to `--progress none`). Directory runs (`refactor_directory_to_fixed_point`) log `[towel]` lines as documented. | Left for a source change |
| 7 | `docs/USAGE_GUIDE.md:418-421` | `proposal.method_kind # instance, class, or static, with insert_into_function for a helper nested in a common enclosing function` and `proposal.source_digests # ... applying a stale proposal raises ChangeConflict("Stale proposal")` | `MethodKind` (`src/towel/unification/models.py:46`) is `Literal["instance", "classmethod", "staticmethod"]` (not `"class"` or `"static"`), `proposal.insert_into_function` (`models.py:213`) is a separate attribute on `RefactoringProposal`, and applying a stale proposal (`src/towel/unification/materialize.py:174`) raises `StaleSource(f"Stale proposal; analyze again: {path}")` (a `ChangeConflict` subclass). | Fixed in `docs/USAGE_GUIDE.md:418-421` |

---

## List of Documentation Edits Made

1. **`docs/QUICKSTART.md:47-54`**:
   - Clarified that without `--no-interactive`, `towel dry` prints the verification-cost summary before `Proceed? (y/N): ` on directories of 25 or more Python files when `--types` is enabled, while smaller targets prompt directly.
2. **`docs/USAGE_GUIDE.md:88-89`**:
   - Documented the fallback behavior of default `tqdm` mode when `tqdm` is not installed (logs a warning once, shows inline bars during analysis, and omits the apply bar) versus `auto` (falls back without warning).
3. **`docs/USAGE_GUIDE.md:418-421`**:
   - Updated `proposal.method_kind` comment to show exact literal values (`"instance"`, `"classmethod"`, or `"staticmethod"`), listed `proposal.insert_into_function` explicitly, and updated `proposal.source_digests` comment to `StaleSource("Stale proposal; analyze again: ...")`.

---

## Verified Invocations, Flags, Defaults, and Sample Outputs

### 1. CLI Commands and `--help` Verification

- **`towel --help`** (`src/towel/cli.py:83-118`):
  - Subcommands: `dry`, `preview`, `rename-helpers`, `recover`.
  - Options: `-h/--help`, `--version` (prints `towel 1.947`).
  - Epilog examples: `towel preview src/`, `towel dry src/ cleaned/`, `towel dry src/ src/` all work; `towel rename-helpers` fails without `target` (Discrepancy #1).
- **`towel dry --help`** (`src/towel/cli.py:338-377`):
  - Positional arguments: `input`, `output`.
  - Flags and defaults verified against `src/towel/cli.py` and `src/towel/unification/defaults.py`:
    - `--interactive` / `--no-interactive` (default: `True`; hidden alias `--non-interactive` also accepted)
    - `--exclude NAME` (default: `[]`, repeatable)
    - `--cross-module` / `--no-cross-module` (default: `False`)
    - `--parameterize-builtins` / `--no-parameterize-builtins` (default: `False`)
    - `--min-lines N` (default: `3`)
    - `--max-parameters N` (default: `5`)
    - `--max-pairs N` (default: `20000000`)
    - `--max-refactorings N` (default: `0`; hidden alias `--max-iterations N` also accepted)
    - `--types` / `--no-types` (default: `True`)
    - `--format` / `--no-format` (default: `True`)
    - `--stats` / `--no-stats` (default: `False`)
    - `--progress {auto,tqdm,none,detail}` (default: `tqdm`)
    - Retired hidden flags `--prefer-absolute-imports` and `--pep420` accepted and log a deprecation warning (`_warn_about_retired_flags`).
- **`towel preview --help`** (`src/towel/cli.py:472-508`):
  - Positional argument: `target`.
  - Shares all `dry` flags above (`--interactive` defaults to `False` and is a no-op for `dry` compatibility), plus `--quick` (default: `False`).
- **`towel rename-helpers --help`** (`src/towel/cli.py:510-625`, checked against `--help` and parser code only):
  - Positional argument: `target`.
  - Flags and defaults: `--complete-scope` (`False`), `--list` (`False`), `--json` (`False`), `--preview` (`False`; hidden alias `--dry-run`), `--rename-file RENAME_FILE` (`None`), `--file FILES` (`None`, repeatable), `--function FUNCTIONS` (`None`, repeatable), `--llm {claude,gpt,copilot,generic}` (default: `generic`).
- **`towel recover --help`** (`src/towel/cli.py:112-117`):
  - Positional argument: `journal` (`Path`).

### 2. Sample Outputs and Offline Execution Checks

- **`README.md:133-168` (`order_summary` / `quote_summary`)**:
  - Executed `towel dry` on the input snippet in an isolated directory with standard-library `python3`; output matched `README.md:150-168` byte-for-byte.
- **`docs/USAGE_GUIDE.md:508-549` (Example 1: `process_user_data` / `process_admin_data`)**:
  - Executed `towel dry` with CLI defaults and Black installed; output matched `docs/USAGE_GUIDE.md:532-549` byte-for-byte.
- **`docs/USAGE_GUIDE.md:560-590` (Example 2: Cross-file discount functions)**:
  - Executed `towel dry package cleaned --no-interactive --cross-module`; confirmed both files remain unchanged and the run declines the 13 candidate pairs with `unsafe_reassignment_block1 7, owned_binding_frame_boundary 5, import_time_effects 1` as documented.
- **`docs/USAGE_GUIDE.md:599-623` (Example 3: `Report.totals` / `Report.refunds`)**:
  - Executed `towel dry`; confirmed the class remains unchanged and declines with `unification_failed 2, owned_binding_frame_boundary 1` (`owned_binding_frame_boundary` on the common tail) as documented.
- **Offline `preview`, `dry`, and API examples on `test_examples/`**:
  - Executed single-file and directory `preview` (complete and `--quick`), `dry` (out-of-place, in-place, `--max-refactorings`, `--progress detail`, `--stats`), and `UnificationRefactorEngine` API snippets (`analyze_file`, `analyze_files`, `analyze_directory`, `plan_refactoring`, `apply_changes`, `refactor_directory_to_fixed_point`) with outputs staged under `docs_check_out/`, then removed `docs_check_out/` via `shutil.rmtree`.
- **`CONTRIBUTING.md` commands**:
  - Verified all `uv`, `pytest`, `coverage`, `black`, `flake8`, `mypy`, `bandit`, `just` recipes, and `scripts/ecosystem_check.py` flags against `pyproject.toml`, `justfile`, `.pre-commit-config.yaml`, and `scripts/ecosystem_check.py --help`.
