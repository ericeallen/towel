# Towel - Usage Guide

> **Supplementary API guide.** A how-to for the `UnificationRefactorEngine` API. Start with the [documentation index](README.md) or the [command-line guide](CLI_GUIDE.md) for CLI use. Current limitations are in [Known limitations](KNOWN_LIMITATIONS.md).

On this page:

- [Getting started with the API](#getting-started-with-the-api)
- [Progress Modes & Termination Reason](#progress-modes--termination-reason)
- [Command-Line Usage](#command-line-usage)
- [Configuration](#configuration)
- [Understanding Proposals](#understanding-proposals)
- [Examples](#examples)
- [Tips](#tips)
- [Troubleshooting](#troubleshooting)
- [Fast local testing](#fast-local-testing)
- [Advanced Usage](#advanced-usage)

## Getting started with the API

There are three ways to use Towel, the code refactoring tool based on anti-unification:

### 1. Analyze a Single File

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

# Analyze one file
proposals = engine.analyze_file("myfile.py")

# Apply best proposal
if proposals:
    refactored = engine.apply_refactoring("myfile.py", proposals[0])
    with open("myfile.py", 'w') as f:
        f.write(refactored)
```

### 2. Analyze Multiple Files (for cross-file duplicates)

Sharing a helper between modules is opt-in: it adds an import between them.

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3, cross_module_helpers=True)

# Analyze multiple files together
files = ["file1.py", "file2.py", "file3.py"]
proposals = engine.analyze_files(files)

# Apply best proposal (handles cross-file)
if proposals:
    modified_files = engine.apply_refactoring_multi_file(proposals[0])

    # Write all modified files
    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)
```

### 3. Analyze an Entire Directory

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

# Reanalyze as changes are applied, writing the completed result to a copy.
results, reason = engine.refactor_directory_to_fixed_point(
    "src/", "src_cleaned/", max_iterations=0, progress="none"
)
```

A proposal belongs to the exact source snapshot analyzed. After applying one,
reanalyze before selecting another that could touch changed files; stale source
is refused. The fixed-point driver handles this sequencing. These API examples
use the engine's explicitly supplied tools; the CLI also constructs the project
checker and formatter from its defaults and options.

## Progress Modes & Termination Reason

When using the fixed-point directory refactoring loop (`refactor_directory_to_fixed_point`) or the `dry` CLI, you can control progress output:

| Mode    | Behavior |
|---------|----------|
| `tqdm`  | Rich progress bar showing applied count, queue length and the proposal being weighed, redrawn on a heartbeat so a long verification does not look like a hung run. |
| `auto`  | Attempts `tqdm`, falls back to a textual single-line bar. |
| `none`  | Suppresses all progress output (quiet for CI). |
| `detail`| Lists the discovered proposals (first 25) and the localized follow-ups after each application, on the `towel` logger at INFO. |

The default is `tqdm`; the CLI's `--progress` option accepts the same values.

Call signature returns `(results_dict, termination_reason)` where `termination_reason` is:

* `fixed_point` – A rehearing, which clears the memory of declined proposals and re-pairs the whole project, applied nothing.
* `iteration_cap` – Stopped after `max_iterations` applied refactorings (the CLI's `--max-refactorings`). The default, 0, runs to a fixed point.

### Example (detail mode)

```python
engine = UnificationRefactorEngine()
results, reason = engine.refactor_directory_to_fixed_point(
    "my_project", "my_project_out", max_iterations=0, progress="detail"
)
print("Termination:", reason)
```

### Localized Follow-Ups

An extraction that would separate a narrowing test, such as an `isinstance` check, from an expression it leaves at the call site is never proposed, with or without type checking.

After each applied proposal, the engine re-analyzes only the changed files to enqueue *localized* follow-up proposals immediately. This accelerates chained extractions without rescanning the entire project every iteration. When that queue drains, a *global* pass re-pairs the project for cross-file duplicates; after the first, a global pass re-pairs only the functions in files rewritten since the previous global pass, which is exact (the argument is in [ARCHITECTURE.md](ARCHITECTURE.md#incremental-global-passes-and-why-they-are-exact)). Construct the engine with `incremental_global_passes=False` to re-pair everything each time; the output is byte-identical.

## Command-Line Usage

Preview opportunities read-only, then refactor into a fresh directory to diff
and adopt:

```bash
towel preview src/
towel dry src/ src_cleaned/ --no-interactive
```

`preview` runs the complete `dry` pipeline in a disposable copy and prints its
final source diff and helper inventory. With the same options, it includes the
same type checking, formatting, repeated extraction and final verification.
`--quick` requests a partial, untyped analysis whose proposals are not the final
result or an upper bound. `dry` writes the verified refactored copy.
`dry` refactors the target inside a private temporary copy of its whole project,
in place or not, so every decision sees the modules around the target (an import
cycle through a module outside the target is seen), and writes nothing until the
run has succeeded, its final type-check confirmation included: the target to the
output directory all at once, or, in place, every file it changed as one
journaled batch. A failed or interrupted run leaves the project as it was.
See the [command-line guide](CLI_GUIDE.md) for CLI options and the [Quick start](QUICKSTART.md) for a first run.

## Configuration

### Engine Parameters

```python
engine = UnificationRefactorEngine(
    max_parameters=5,  # Maximum parameters for extracted functions
                       # (prevents over-parameterization)

    min_lines=3        # Minimum lines for a code block
                       # (smaller blocks are ignored)
)
```

Both are `--max-parameters` and `--min-lines` on the command line.

The remaining constructor options are keyword-only after `parameterize_constants`.
The table gives API defaults; the CLI separately attaches configured checker
and formatting tools, while the bare API defaults for those callbacks are `None`:

| Parameter | Default | Effect |
|---|---|---|
| `parameterize_constants` | `True` | Differing constants become helper parameters. |
| `parameterize_builtins` | `False` | Permit each caller's builtin lookup to reach a shared helper, normally as `lambda: len` or `lambda: print`. Without it, cross-module bare builtin reads decline (except `__debug__`); same-module reads stay bare. Only the existing first-effect, single-use proof permits eager evaluation. Typed thunks retain the callable result, e.g. `Callable[[], Callable[..., int]]` for `lambda: len`. Blocks that differ in which builtin they use still decline. |
| `cross_module_helpers` | `False` | Also share a helper between duplicates in different modules, importing it into the others (`--cross-module/--no-cross-module`). Off, only duplicates within a module are paired and no import of a project module that runs is written. |
| `excluded_directories` | `()` | Names of directories or files to leave unchanged (`--exclude`), at any depth. The checks that read the whole program still read them, and one that does not parse is taken for no part of the program; the program's import model reads nothing in an excluded directory. |
| `max_candidate_pairs` | `20_000_000` | Most candidate block pairs one analysis evaluates; past it the largest groups of similar blocks are left out with a warning (`--max-pairs`). |
| `skip_trivial_helpers` | `True` | Apply optional forwarding and tiny straight-line interface-cost filters. `False` bypasses those output-quality filters, not semantic checks or unconditional no-logic refusals. See [the calibration](DECISIONS.md#2026-10-06-tiny-helpers-must-repay-their-interface-cost). |
| `annotate_helpers` | `True` | Copy the annotations the call sites declare onto the helper, in code that uses annotations. |
| `type_oracle` | `None` | A `TypeOracle` (`towel.type_inference`) that reveals types, decides subtyping, and checks generated code; without one no type inference or project type validation runs. Binding, lifetime and instantiation checks remain active; the CLI wires its oracle through `--types/--no-types`. |
| `snippet_formatter` | `None` | Formats each inserted snippet; see below (`--format/--no-format`). |
| `file_finisher` | `None` | Finishes each modified file, for example by sorting its imports: called with the file's path and new text, while the file at that path still holds the text the change started from. |
| `incremental_global_passes` | `True` | Later global passes re-pair only rewritten files (exact). The rehearing that ends a run re-pairs everything regardless. |
| `promote_equal_hof_literals` | `False` | Explicit API opt-in to expose equal literal arguments of higher-order factory calls as helper parameters after all sites pass its checks. The CLI has no corresponding flag. |
| `settings` | `None` | A `towel.diagnostics.Settings`: what Towel reads from the environment (worker cap, debug switches). When omitted, the engine reads the environment once at construction; the command line and the analysis session each read it once as well (see *Diagnostics and settings* in [ARCHITECTURE.md](ARCHITECTURE.md)). |

`reuse_existing_functions` is deprecated and does nothing: a duplicate that is
the whole body of a function once made the other sites call that function, and
since 1.772 an extracted input function calls a fresh helper instead, because
a call to the
existing function looked it up in its module each time, so patching or
rebinding it changed both. The keyword is still accepted, and will be removed
in a later release.

`prefer_absolute_imports` and `pep420_namespace_packages`, and the
`--prefer-absolute-imports` and `--pep420` flags, are deprecated and do
nothing either. They chose how a cross-file helper's import was spelled from
packaging metadata; since 1.772 every import Towel writes is spelled as the
program's own imports show it works (see *How a cross-module import is
spelled* in the [cross-module guide](CROSS_MODULE.md)). They are still accepted, a run given one of the flags says
it had no effect, and they will be removed in a later release.

### How a cross-module import is spelled

The [cross-module guide](CROSS_MODULE.md) explains host selection, import spelling,
packaging boundaries and remedies for ambiguous module names. The same rules
apply to CLI and API callers.

### Formatting and type-checker setup

The CLI's `dry` command wires the formatter, import sorter, and type oracle
from the project's own configuration. Library callers can do the same:

```python
from pathlib import Path
from towel.formatting import formatter_for_project, import_sorter_for_project
from towel.type_inference import type_oracle_for_project

root = Path("src/")
formatter = formatter_for_project(root)   # ruff when configured, else Black
sorter = import_sorter_for_project(root)  # ruff's I rules or isort, when the project uses them
oracle = type_oracle_for_project(root)    # mypy, pyright, or both

engine = UnificationRefactorEngine(
    snippet_formatter=formatter.tool, file_finisher=sorter.tool, type_oracle=oracle.tool
)
```

Each result is a `ToolChoice`: its `tool` is None when nothing suitable is
installed, and its `note` says what was chosen and names a configured tool
that is not installed. The checker is the exception: a project that configures
mypy or pyright is checked by that checker or not at all, so
`type_oracle_for_project` raises `CheckerNotInstalled` when a configured one is
not installed, rather than substitute another checker or none (pass
`type_oracle=None` and `annotate_helpers=False` for an unverified run). A
project that configures neither is checked by mypy when it is installed, with
mypy's defaults: the check is the one `mypy` itself makes of the same files,
and only the probes that infer a helper's types also check the bodies of
functions without annotations, where mypy otherwise reveals nothing but `Any`.
Without a formatter the rendering is `ast.unparse`'s: one
statement per line, single-quoted strings, no blank-line conventions.

Each tool keeps to the files its own configuration selects. The formatter
leaves the inserted code as rendered where the project excludes the path it
was chosen for from formatting (`ruff format`'s exclusions through
`--force-exclude`; Black's `exclude`, `extend-exclude` and `force-exclude`).
The import sorter leaves a file alone that ruff's `exclude`,
`extend-exclude`, `lint.exclude` or `per-file-ignores`, or isort's `skip`,
`extend_skip`, `skip_glob`, `extend_skip_glob` or `skip_gitignore`, leave
out, judged for the project's own file even while a run works on a staged
copy of it. It sorts only a file it already leaves as it is: every import
runs its module where it stands, so a file's own imports keep their order,
and only the ones Towel added may move. A file whose imports the sorter
would change keeps Towel's imports where Towel placed them, and so does
one where the sorter's result would move an import the file already had;
the run logs each such file once. `sort_added_imports` makes that decision
on texts, and `SortOutcome` names what came of it.

The engine checks the original project before using its type oracle, and
logs what that check reports (the `towel` logger, which the CLI prints on
stderr): how many errors, in which files, and which files leave a name the
checker cannot type. The errors are left as they are. Every later check is
compared with them, and a change is rejected only for an error they do not
account for. In a file no change has touched an error must match one at the
same line. In a file a change has touched, the texts before and after are
aligned by a line diff, and an error is accounted for only by the original's
error where it stood:

- on a line the change left alone, by the original's error on that line,
  wherever the line now stands;
- in the helper's body, by an error with the same message at the same
  statement of one copy of the block the helper was made from, each error
  once, the copy chosen being the one that leaves the fewest errors
  unexplained. Merging two copies into one helper frees the other copy's
  errors, and they account for nothing, so a new error cannot hide behind a
  message the second copy happened to share;
- anywhere else the change wrote (a call site, an import), by an error with the
  same message on the lines that stretch of the diff replaced, which for a call
  site is its own copy.

Any other error is new, and so is every error in a changed file whose text
before or after is unknown. A message that names a line (mypy's `Name "x"
already defined on line 12`) therefore reappears as new when its line moves,
and rejects the change rather than hide an error. Once a driver writes a
change, the change's own check is the reference for the next, so an error one
change removed cannot be spent by another; direct `apply_refactoring` calls,
whose results the engine does not see written, keep comparing with the
original's errors, aligning each file they changed with the original's text. The final cold check
compares the same way; an error only it reports is then looked for in a cold
check of the original, since a checker started from nothing can disagree with a
warm one about files no change touched, and only one the original lacks too
refuses the run.

A proposal that would change a file where the original check leaves a name the
checker cannot type is declined with `UnverifiableChangeError` and counted as
`not verifiable: its file holds a name the type checker cannot type` in
`engine.run_report`. Such a name comes from an import the checker cannot
resolve or finds no types for (mypy's `import-not-found` and `import-untyped`,
pyright's `reportMissingImports` and `reportMissingTypeStubs`), an untyped
decorator, or a base class of type `Any`; whatever it reaches is `Any`, which
accepts every use, so no check could see a misuse. Installing the missing
module or its stubs where Towel runs is what has such a file refactored with
types.

Code the checker does not look at is not changed either. A checker takes
code to be unreachable where the platform and Python version it checks for
make a `sys.platform`, `sys.version_info` or `TYPE_CHECKING` test false,
after an `assert` it knows fails, or where the declared types rule it out
(a `return NotImplemented` after an `isinstance` test an annotated argument
always passes, which no platform checks), and reports nothing there. The engine asks
it where it looks (`towel.reachability`: a `reveal_type` placed before each
statement is answered only there): before the run, the inferring checker, about
the start of every block of the analyzed files, which it logs and does not
change; and before it accepts any change, every configured checker, about each
statement on the lines the change writes. A proposal refused this way raises
`UncheckedCodeError` and is counted as `not verifiable: the type checker does
not look at the code it changes`.

Only a checker that cannot run at all refuses the run: a crash, a timeout, a
plugin or configuration it cannot load. Fix what stops it, or rerun the CLI
with `--no-types`, which preserves existing source annotations but generates
unannotated helpers; library callers obtain the same behavior with
`type_oracle=None` and `annotate_helpers=False`. Prospective-project
verification stays enabled throughout every other run.

mypy runs with the project's configured plugins, loaded exactly as the
project's own mypy loads them: a plugin module from the environment Towel runs
in, a `.py` plugin path relative to the configuration file. Plugins decide what
expressions' types are, so a check without them would not be the project's;
they execute as they do in the project's own mypy run. A plugin that cannot be
loaded there (not installed where Towel runs, or unable to import the project
itself) fails the baseline check, so the run is refused before anything is
written, with mypy's own message.

For new module-level helpers and helper methods, Towel also anti-unifies the
corresponding argument and result types. For example, `list[int] -> int` and `list[str] -> str` can become
`list[T] -> T`. Already-generic callers receive fresh helper binders with their
supported bounds or constraints preserved. The generated `TypeVar` declarations
work on Python 3.11 and newer. Instance and class helpers retain parameters
bound by the host class; static helpers infer fresh parameters from explicit
arguments. Fresh method parameters are declared before the host class
at module scope. Every candidate is checked with its rewritten calls
and unchanged consumers before it is accepted; `--no-types` does not synthesize
generic contracts. See the [design and supported boundaries](proposals/type-parameters.md).

Each fixed-point call starts a new run and checks the original before creating
an output copy. Direct `apply_refactoring` calls share an implicit run; call
`engine.begin_refactoring_run(paths)` when starting a separate run on the same
engine. Closing it matters more than it used to: an oracle that verifies with
pyright holds a long-lived language server process and a private copy of the
project on disk for as long as it lives, and one that infers with mypy holds a
worker process and its cache directory. The caller owns the oracle and should
call `oracle.tool.close()` in a
`finally` block when `oracle.tool` is not `None`.

### Directory Scanning

```python
# Recursive (default) - searches subdirectories
proposals = engine.analyze_directory("src/", recursive=True)

# Non-recursive - only files in the directory itself
proposals = engine.analyze_directory("src/", recursive=False)
```

The scanner automatically skips:
- Hidden directories (starting with `.`)
- `__pycache__`
- `node_modules`, and every environment: a directory holding a `pyvenv.cfg` or a
  `conda-meta`, whatever its name (a package of your own called `env` or `venv`
  is analyzed like any other)
- The directories and files `excluded_directories` names (`--exclude`)
- Symlinked files, and Towel's own `_towel_probe_*.py` type-checker probes

A run (`refactor_to_fixed_point`, `refactor_directory_to_fixed_point`, and
`towel dry` and `towel preview` in every mode) first reads the whole program,
from the project root down, less those directories and the ones every scan
skips (`towel.program_files`). If a file there does not parse on the Python
Towel runs on, the run raises `UnparsedProgramError` (a `ValueError`) before
anything is written, naming each file and what the parser said: it may run on a
newer Python, where what it does would go unseen by the checks that decline
unsafe changes. A file `excluded_directories` names is read like any other, and
only one that does not parse is passed over. The refusal suggests, for each
file, a name that clears it: its directory's, when two or more share it, else
its own. `analyze_files` makes no such check first, but refuses the same
way at the first pair it judges.

## Understanding Proposals

Each proposal contains:

```python
proposal.description          # "Extract common code from func1 and func2"
proposal.parameters_count     # Number of parameters in extracted function
proposal.extracted_function   # The AST of the new function
proposal.replacements         # List of Replacement dataclasses: line_range, node (the
                              # generated call statement), file_path, class_name,
                              # method_kind, implicit_param
proposal.file_path            # Canonical location for the extracted function
proposal.reused_function      # ReusedFunction(name, file_path, line_range) on a proposal
                              # for a compatible helper generated earlier in this run;
                              # input functions retain independent bindings
proposal.required_imports     # Imports the host needs for the helper's annotations
proposal.helper_type_declarations  # Fresh generic declarations, materialized with the helper
proposal.helper_comments      # The sites' comments the helper carries (see Comments below)
proposal.return_variables     # Names the helper returns, in the call's unpacking order
proposal.insert_into_class    # The class the helper becomes a class-private method of,
                              # if any: only ever the class holding every site;
proposal.method_kind          # instance, class, or static, with insert_into_function
                              # for a helper nested in a common enclosing function
proposal.source_digests       # The file digests the proposal was computed from; applying
                              # a stale proposal raises ChangeConflict("Stale proposal")
```

### Protect a function from refactoring

The API honors the same source directive as the CLI:

```python
def frame_sensitive(value):  # towel: no-extract
    return inspect_caller(value)
```

The exact comment belongs immediately after the signature's final colon,
including on the closing line of a multiline signature. The protected
definition and nested bodies stay intact across discovery, clustering,
reuse, helper insertion and later passes. Other code remains eligible.
See [Protect a function](CLI_GUIDE.md#protect-a-function) for syntax and limits;
this is user-directed protection, not automatic reflection detection.

### Comments

The helper carries the comments of the blocks it replaces, beside the code
they were written for; `proposal.helper_comments` holds them, and each
`replacement.comments` holds its own block's. Comments above or below a
block stay at its call. An explanatory comment is kept from every site that
has one. A tool directive (`# type: ignore`, `# pyright: ignore`, `# ty:
ignore`, `# pyrefly: ignore`, `# zuban: ignore`, `# pyre-ignore`, `# noqa`,
`# ruff: ...`, `# pragma: no cover`, `# nosec`, `# nosemgrep`, `# pylint:
...`, `# lint-ignore`, `# fmt: ...`, `# isort: ...`, a type comment) must be
carried alike by every site, and must not reach code that becomes an
argument of the call, anything but a name or a literal, since that code is
written at the call site where the directive does not reach. An ignore on a
line of its own reaches the next line of code, as ty, ruff, pyre, pyrefly,
Semgrep and Fixit read it, so one above a block's first statement, which
would stay above the call, declines the pair too.
Nor may a coverage pragma or pylint `disable` around the blocks (on an
enclosing `def`, `if`, loop or `else:` line), or a region directive opened
before them and closed after them (`# fmt: off`, `# ruff: disable[E501]`,
`# pylint: disable` on a line of its own, and the like), govern every site
where the helper would be written outside it; a formatter's region may not
govern any site; nor may coverage exclude a block's first statement, since
the call replacing the block would be measured. Lines a formatter directive
keeps as written (`# fmt: off` to `# fmt: on`, `# fmt: skip`) are copied into
the helper as the first site wrote them, uniformly re-indented; the pair is
declined (`layout_not_kept`) when the sites write them differently, a
parameter would stand in them, or they cannot be re-indented unchanged.
What coverage excludes is read from the project's own coverage.py
configuration (`.coveragerc`, `.coveragerc.toml`, `setup.cfg` or `tox.ini`
`[coverage:report]`, `pyproject.toml` `[tool.coverage.report]`, or the file
`COVERAGE_RCFILE` names), so a line its `exclude_lines` or `exclude_also`
matches counts as a `# pragma: no cover` line, and the pragma counts only
where the configuration keeps it; a configuration coverage.py could not
read is reported and replaced by its defaults. A pair
that breaks any of these is declined, and `engine.declined_pairs` counts it
under `directives_differ`, `directive_on_argument`,
`directive_outlives_block`, `directive_around_block`,
`excluded_block_start`, `directive_on_shared_line` or `layout_not_kept`; a
directive is never
copied onto a call or a
helper's `def` line.

### Cross-File vs Same-File

Cross-file proposals come only from an engine made with `cross_module_helpers=True`.

```python
# Check if it's cross-file
is_cross_file = any(r.file_path not in (None, proposal.file_path) for r in proposal.replacements)

if is_cross_file:
    # Multiple files affected - use multi-file method
    modified_files = engine.apply_refactoring_multi_file(proposal)
else:
    # Single file - simpler method works
    refactored = engine.apply_refactoring(proposal.file_path, proposal)
```

## Examples

The outputs below are what `towel dry` writes with the defaults and Black
installed. Through the API without a `snippet_formatter`, strings come out
single-quoted and the layout is `ast.unparse`'s.

### Example 1: Simple Validation Code

**Before:**
```python
def process_user_data(user_id):
    user = {"id": user_id, "name": "John"}
    if not user.get("id"):
        raise ValueError("User ID is required")
    if not user.get("name"):
        raise ValueError("User name is required")
    if len(user.get("name", "")) < 2:
        raise ValueError("User name too short")
    return user

def process_admin_data(admin_id):
    admin = {"id": admin_id, "name": "Jane", "role": "admin"}
    if not admin.get("id"):
        raise ValueError("User ID is required")
    if not admin.get("name"):
        raise ValueError("User name is required")
    if len(admin.get("name", "")) < 2:
        raise ValueError("User name too short")
    return admin
```

**After:**
```python
def __extracted_func_0(__param_0):
    if not __param_0.get("id"):
        raise ValueError("User ID is required")
    if not __param_0.get("name"):
        raise ValueError("User name is required")
    if len(__param_0.get("name", "")) < 2:
        raise ValueError("User name too short")
    return __param_0


def process_user_data(user_id):
    user = {"id": user_id, "name": "John"}
    return __extracted_func_0(user)

def process_admin_data(admin_id):
    admin = {"id": admin_id, "name": "Jane", "role": "admin"}
    return __extracted_func_0(admin)
```

The trailing `return` joined the block because it is the same statement in
both functions up to the renamed variable, so the call site becomes a
`return` of the helper.

### Example 2: Cross-File Duplicates

With `--cross-module`, for two modules of one package that neither import
their package absolutely.

**Before (file1.py):**
```python
def calculate_discount_for_regular_customer(price, customer):
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price
```

**Before (file2.py):**
```python
def calculate_discount_for_premium_customer(price, customer):
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price
```

**After (file1.py):**
```python
def __extracted_func_0(customer, price):
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price


def calculate_discount_for_regular_customer(price, customer):
    return __extracted_func_0(customer, price)
```

**After (file2.py):**
```python
from .file1 import __extracted_func_0
def calculate_discount_for_premium_customer(price, customer):
    return __extracted_func_0(customer, price)
```

The duplicate is the whole body of both functions, and each becomes a call of
the new helper: neither is rewritten to call the other, since such a call
looks the other function up in its module every time, so patching or rebinding
it would change both. The import is relative because both modules sit in one
package.

### Example 3: Methods

**Before:**
```python
class Report:
    def __init__(self, rows):
        self.rows = rows
        self.__cache = {}

    def totals(self):
        values = [row["amount"] for row in self.rows if row["amount"] > 0]
        total = sum(values)
        self.__cache["total"] = total
        return f"total {total:.2f}"

    def refunds(self):
        values = [row["amount"] for row in self.rows if row["amount"] < 0]
        total = sum(values)
        self.__cache["total"] = total
        return f"total {total:.2f}"
```

**After:**
```python
class Report:
    def __init__(self, rows):
        self.rows = rows
        self.__cache = {}

    def totals(self):
        values = [row["amount"] for row in self.rows if row["amount"] > 0]
        return self.__extracted_func_0(values)

    def refunds(self):
        values = [row["amount"] for row in self.rows if row["amount"] < 0]
        return self.__extracted_func_0(values)

    def __extracted_func_0(self, values):
        total = sum(values)
        self.__cache["total"] = total
        return f"total {total:.2f}"
```

A block that methods of one class share becomes a method of that class, with a
class-private name, which the class stores as `_Report__extracted_func_0`,
avoiding accidental overrides by ordinary differently named subclasses.
Explicit mangled-name writes and same-named subclasses require the
[documented hygiene checks and scope limits](KNOWN_LIMITATIONS.md#method-insertion). Written in the class's body, the helper reads
`self.__cache` as the methods did. A block shared by methods of different
classes, siblings or a parent and its child, becomes a module-level function
that takes the receiver as an argument instead: Towel never adds a method to a
class that did not already hold the code, and whether such a function belongs
in a base class or a mixin is left to your review.

### Decorated code

Ordinary nonreflective decorator behavior remains subject to Towel's usual
correctness checks. Reflection and instrumentation that read or transform a
function's source, AST, bytecode or class structure are outside the guarantee,
whether applied through `@`, an ordinary call, an import hook or a class hook.
Towel does not scan for these behaviors or refuse extraction to protect them.

For example, moving a body into a helper can change what typeguard checks or
numba compiles; moving an assertion can change pytest's rewritten failure
report; moving an inline-snapshot call can change the source it reads. These
are unsupported observations, not detected refusal categories. Review such
changes using your project's tests and requirements. See the
[reflection and instrumentation limitations](KNOWN_LIMITATIONS.md#decorators-that-compile-or-instrument-a-body).

## Tips

### 1. Always Use analyze_files() or analyze_directory() for Cross-File

```python
engine = UnificationRefactorEngine(cross_module_helpers=True)

# ❌ BAD - Misses cross-file duplicates
proposals1 = engine.analyze_file("file1.py")
proposals2 = engine.analyze_file("file2.py")

# ✅ GOOD - Finds cross-file duplicates
proposals = engine.analyze_files(["file1.py", "file2.py"])
```

### 2. Review Proposals Before Applying

```python
proposals = engine.analyze_directory("src/")

# Review each proposal
for i, prop in enumerate(proposals, 1):
    print(f"{i}. {prop.description}")
    print(f"   Parameters: {prop.parameters_count}")
    print(f"   Function: {prop.extracted_function.name}")
    print()

# Selectively apply
best_proposal = proposals[0]
```

### 3. Iterative Refactoring

Running the tool multiple times can find more opportunities:

```python
# First pass
proposals = engine.analyze_directory("src/")
# Apply proposals...

# Second pass - may find more after first refactoring
proposals2 = engine.analyze_directory("src/")
```

### 4. Filter by Parameters

```python
# Only apply refactorings with few parameters (cleaner)
simple_proposals = [p for p in proposals if p.parameters_count <= 2]
```

## Troubleshooting

### "No proposals found"

- Check `min_lines` - code blocks might be too small
- Check `max_parameters` - duplicates might differ in too many ways
- Differing constants become parameters by default; with `parameterize_constants=False` they must match
- Check `engine.declined_pairs` for the binding, control-flow or placement constraint that prevented extraction

### "Too many proposals"

- Increase `min_lines` to focus on larger duplicates
- Decrease `max_parameters` to avoid over-parameterized functions
- Raise `DEFAULT_SIMILARITY_THRESHOLD` in `block_signature.py` (0.6; it is not an
  engine option) so that fewer loosely similar pairs reach unification

### Import errors after refactoring

Towel spells a cross-file helper's import the way the program's own imports
show it works (*How a cross-module import is spelled* above), so the result
imports wherever the original did, in place or once an out-of-place copy is
adopted into its real location. If you still hit an import error:

- Refactor through `analyze_files()`/`analyze_directory()` or the `towel dry`
  command, which run the full pipeline including the import model; a
  hand-built `RefactoringProposal` skips host selection, and its import is
  refused when no spelling is known.
- Run Towel with the interpreter the project uses: an installed copy of the
  package, or a library named like one of its directories, that only another
  interpreter holds is invisible to it unless the project declares it.

### "No helper shared across modules"

- Pass `--cross-module`: by default only duplicates within a module are paired.
- If the run names import problems, follow the remedy it prints for each
  kind (*How a cross-module import is spelled* above): `--exclude` for a
  stray copy in the tree, another environment for an installed copy, and a
  rename or `--exclude` for a directory named like a required distribution.
- A file the run names as importing a module the tree lacks is left
  unchanged. If it is a package's `__init__.py` importing a generated
  `_version.py`, install the project (`pip install -e .`) so the module
  exists, and its modules share helpers with the rest of the project again.

## Fast local testing

When developing or iterating on changes, run the fast smoke suite to validate core behavior and output stability without the cost of the full test run:

```
just test-smoke
```

Use this during inner-loop development; reserve the full suite for pre-commit or release gating.

## Advanced Usage

### Custom Filtering

```python
def is_good_proposal(proposal):
    """Filter criteria for good refactorings."""
    # Must have few parameters
    if proposal.parameters_count > 3:
        return False

    # Must save significant code
    lines_saved = sum(r.line_range[1] - r.line_range[0] for r in proposal.replacements)
    if lines_saved < 10:
        return False

    return True

good_proposals = [p for p in proposals if is_good_proposal(p)]
```

### Dry Run

```python
# Preview without writing
for proposal in proposals:
    print(f"\nProposal: {proposal.description}")
    print("Extracted function:")
    print(ast.unparse(proposal.extracted_function))
    print()
```

### Selective Application

```python
from towel.changes import apply_changes

proposals = engine.analyze_directory("src/", progress="none")
selected = next(
    (proposal for proposal in proposals if "utils" in proposal.file_path),
    None,
)
if selected is not None:
    apply_changes(engine.plan_refactoring(selected))
# Analyze again before selecting another proposal.
```
