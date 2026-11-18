# Python API guide

```python
from towel.api import UnificationRefactorEngine

engine = UnificationRefactorEngine()
results, reason = engine.refactor_directory_to_fixed_point("src/", "src_cleaned/", progress="none")
print(results)
print(reason)
```

```text
{'src_cleaned/accounts.py': (1, ['Extract common code from register_user and register_admin'])}
fixed_point
```

This guide is for calling Towel from Python, through `UnificationRefactorEngine`.
For the command line, see the [command reference](CLI_GUIDE.md); for what Towel
will not change, [known limitations](KNOWN_LIMITATIONS.md). Every option and
attribute is in [the Python API, in full](design/PYTHON_API_REFERENCE.md).

## Getting started with the API

The supported library entry point is `towel.api`. It exports the engine, its
proposal and change-plan types, publication function and catchable exceptions.
The [compatibility contract](COMPATIBILITY.md#python-api) distinguishes supported
operations from advanced implementation hooks. The CLI additionally configures
formatters and checkers from the project; the examples below use the tools the
caller explicitly supplies.

There are three common workflows.

### 1. Analyze a Single File

```python
from towel.api import UnificationRefactorEngine, apply_changes

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

# Analyze one file
proposals = engine.analyze_file("myfile.py")

# Apply one proposal with stale-source checks and recoverable byte replacement.
if proposals:
    apply_changes(engine.plan_refactoring(proposals[0]))
```

### 2. Analyze Multiple Files (for cross-file duplicates)

Sharing a helper between modules is opt-in: it adds an import between them.

```python
from towel.api import UnificationRefactorEngine, apply_changes

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3, cross_module_helpers=True)

# Analyze multiple files together
files = ["file1.py", "file2.py", "file3.py"]
proposals = engine.analyze_files(files)

# Apply every file changed by one proposal as a recoverable batch.
if proposals:
    apply_changes(engine.plan_refactoring(proposals[0]))
```

### 3. Analyze an Entire Directory

```python
from towel.api import UnificationRefactorEngine

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

`plan_refactoring` preserves each file's encoding, BOM and newline convention;
`apply_changes` checks original bytes and publishes a recoverable batch. The
lower-level `apply_refactoring` and `apply_refactoring_multi_file` methods only
return rendered text. Callers using those methods own byte encoding and safe
publication; ordinary text-mode writes do not preserve all source formats.

## Results and errors

`refactor_directory_to_fixed_point` returns `(results, reason)`: `results`
maps each changed output file to a pair, the number of refactorings it received
and a list describing each one. The new text is in the output directory. `reason`
is `fixed_point` when no more refactorings were found, or `iteration_cap` when `max_iterations` (the
CLI's `--max-refactorings`) stopped the run; 0, the default, means no cap.
`progress` is `auto` by default, which draws a bar only on a terminal;
`"none"` turns it off.

In relaxed mode, the default, a file that does not parse on the Python Towel
runs on is skipped with a warning. In strict mode the run raises
`UnparsedProgramError` (a `ValueError`) before anything is written, naming each
such file and the `--exclude` that would leave it out. Applying a proposal
whose files changed since it was computed raises `StaleSource`.

## Command-Line Usage

The command line runs the same engine: `towel preview src/` shows what a run
would change, and `towel dry src/ cleaned/` writes the result
([command reference](CLI_GUIDE.md)). `dry` refactors the target inside a
private copy of its whole project, so every decision sees the modules around
it, and writes nothing until the run, its final type check included, has
succeeded: to a new output all at once, or, in place, every changed file as
one journaled batch. A failure caught during the write is rolled back. An
in-place write that is killed part-way can leave some files changed and others
not; it also leaves a `.towel-transaction-<id>` journal, and
`towel recover` restores the original files from it, as
[recovery and interrupted writes](CLI_GUIDE.md#recovery-and-interrupted-writes)
describes. For a first run, see the [quick start](QUICKSTART.md).

## Options

```python
engine = UnificationRefactorEngine(
    max_parameters=5,  # most differences that may become helper parameters
    min_lines=3,       # shortest block or expression considered
)
```

Both are `--max-parameters` and `--min-lines` on the command line. A third
positional argument, `parameterize_constants`, defaults to `True`. The other
options are keyword-only; these are the ones most callers set:

| Option | Default | Effect |
|---|---|---|
| `strict` | `False` | Strict mode, `--strict` ([relaxed and strict](RELAXED_AND_STRICT.md)). |
| `cross_module_helpers` | `False` | Share a helper between modules, `--cross-module` ([sharing across modules](CROSS_MODULE.md)). |
| `excluded_directories` | `()` | Names of directories or files to leave unchanged, `--exclude`. |
| `complete_scope` | `False` | Declare that the target holds every file that uses its code, `--complete-scope`. |
| `expression_candidates` | `True` | Also extract a repeated expression of at least `min_lines` lines. |
| `include_ignored` | `False` | Also analyze files Git ignores, `--include-ignored`. |

The keywords `reuse_existing_functions`, `prefer_absolute_imports` and
`pep420_namespace_packages` are deprecated and do nothing; remove them from your
calls. The [compatibility contract](COMPATIBILITY.md#python-api) lists the options
it supports; `complete_scope`, `expression_candidates` and `include_ignored`
are not among them yet, so pin the Towel version if you rely on them.

The engine checks types and formats code only when you give it a type oracle
and a formatter; the command line builds both from the project's
configuration. [Formatting and type-checker setup](design/PYTHON_API_REFERENCE.md#formatting-and-type-checker-setup)
shows how.

## Understanding Proposals

`proposal.description` says what a proposal does, such as "Extract common
code from func1 and func2" or "Reuse NAME" when the copies call a function
that already exists. Only `description` is part of the supported API; the
other attributes are outside the supported `towel.api` surface, where a
proposal is opaque, so pin the exact Towel version before reading them
([every attribute](design/PYTHON_API_REFERENCE.md#understanding-proposals)).

A proposal belongs to the source it was computed from: after applying one,
analyze again before choosing the next.

### Selective Application

```python
from pathlib import Path

from towel.api import UnificationRefactorEngine, apply_changes

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
files = sorted(str(path) for path in Path("src").glob("*.py"))
plans = (
    engine.plan_refactoring(proposal)
    for proposal in engine.analyze_files(files, progress="none")
)
selected = next(
    (plan for plan in plans if any("utils" in str(change.path) for change in plan.changes)),
    None,
)
if selected is not None:
    apply_changes(selected)
# Analyze again before selecting another proposal.
```

[Documentation index](README.md)
