# Towel

> “A towel ... is about the most massively useful thing an interstellar hitchhiker can have.”
>
> [Douglas Adams](https://www.goodreads.com/quotes/24779-a-towel-the-hitchhiker-s-guide-to-the-galaxy-says-is),
> [*The Hitchhiker's Guide to the Galaxy*](https://en.wikipedia.org/wiki/Towel_Day#Origin)

Towel finds repeated Python code and proposes helper function extractions.

> **Install from PyPI as [`code-towel`](https://pypi.org/project/code-towel/)** (the command is `towel`):
> `pip install code-towel`
> Do **not** install `towel`: `pip install towel` and `uvx towel` fetch a different, unrelated project.

**Release status: 1.772 (beta).** Since 1.732, an extracted helper can keep the
relationships among its argument and return types instead of losing them to
`Any`: anti-unifying the types alongside the code gives `list[T] -> T` where the
call sites use `list[int] -> int` and `list[str] -> str`. Generic methods keep
the type parameters their host class already binds.

The same release made the typed path usable on a large project. Verification,
not analysis, is what a run with type checking spends its time in, and Sphinx's
243 modules with mypy and Pyright both strict used to run for hours without
finishing; it now reaches a fixed point in well under one, after which Sphinx's
own test suite reports exactly what it reported before. An extraction that
would separate a narrowing test from code depending on it is declined outright,
with or without type checking. See the
[1.772 changelog](https://github.com/ericeallen/towel/blob/v1.772/CHANGELOG.md#1772---2026-09-21)
for details.

**New here?** The [Quick start](https://github.com/ericeallen/towel/blob/v1.772/docs/QUICKSTART.md) gets you from install to a reviewed refactoring in four steps.

## What it does

Towel finds code that is repeated across your functions and pulls each group of
duplicates into one shared helper, rewriting the copies as calls to it. It works
by *anti-unification*, following the work of
[Reynolds and Plotkin](https://github.com/ericeallen/towel/blob/v1.772/docs/ARCHITECTURE.md#references): it computes the
least-general generalization of the matching blocks, so the parts that are the
same become the helper's body and the parts that differ become its parameters.

Given two functions that share a block:

```python
def order_summary(order):
    items = [i for i in order.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return f"Order {order.id}: ${total}"

def quote_summary(quote):
    items = [i for i in quote.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return f"Quote {quote.id}: ${total}"
```

Towel proposes the shared block as a helper (emitted with a placeholder name you
rename afterward) and rewrites both functions to call it. The comments of the
moved code go into the helper beside the code they were written for, a
`# type: ignore` or `# pragma: no cover` included; duplicates whose tool
directives differ are left alone, since the helper would have one line where
they had two. The inserted code is
formatted the way the project formats its own (`ruff format` when the project
configures ruff, else Black, at the line length the project declares) and any
imports Towel adds are sorted with ruff's import rules or isort when the
project uses them, all when installed (`pip install "code-towel[format]"`);
`--no-format` turns that off. In annotated code the helper also carries the parameter and return
annotations its call sites declare, and, when the project's type checker is
installed (mypy or pyright; `pip install "code-towel[types]"`), the types it
infers for the rest, verified against the checker; `--no-types` turns that
off:

```python
def __extracted_func_0(__param_0):
    items = [i for i in __param_0.items if i.in_stock]
    subtotal = sum((i.price for i in items))
    total = round(subtotal * 1.08, 2)
    return total

def order_summary(order):
    total = __extracted_func_0(order)
    return f"Order {order.id}: ${total}"

def quote_summary(quote):
    total = __extracted_func_0(quote)
    return f"Quote {quote.id}: ${total}"
```

When a duplicate is the whole body of an existing function, that function gets
a call of the new helper like every other copy; it is never rewritten to call
another function that restates it, since patching or rebinding that one would
then change both. A block that methods of one class share becomes a method of
that class with a class-private name (`self.__extracted_func_0(...)`), which no
subclass can override; a block shared across classes becomes a module-level
function that takes the receiver, and whether it belongs in a class is left to
your review: Towel never adds a method to a class that did not already hold the
code.

Towel checks each proposed extraction by instantiating the helper with each
call's arguments and comparing it with the block it replaces. It also checks
name binding, control flow, and evaluation order, and declines transformations
that fail these checks. It skips trivial extractions that would add
indirection without sharing real logic, wraps arguments that must not be
evaluated eagerly in zero-argument `lambda`s (see
[below](#why-some-arguments-are-wrapped-in-lambda)), and leaves naming to you.
Nothing is written without your say-so: the workflow is preview, refactor into a
copy, review the diff, and run your tests.
The [known limitations](https://github.com/ericeallen/towel/blob/v1.772/docs/KNOWN_LIMITATIONS.md) describe behavior outside
these checks; the [readiness report](https://github.com/ericeallen/towel/blob/v1.772/docs/PRODUCTION_READINESS.md) records the
validation evidence.

## Install

The PyPI package is **`code-towel`**; installing it gives you the **`towel`** command.

```bash
pip install code-towel
towel --version
towel --help
```

To update an existing installation, run `pip install --upgrade code-towel`.

Install `code-towel`, not `towel`: the name `towel` on PyPI is a different, unrelated project, so `pip install towel` and `uvx towel` will not install this tool. To run it with `uvx` without installing, name the package explicitly:

```bash
uvx --from code-towel towel --help
```

The runtime uses only the standard library; optional `tqdm` provides progress bars, which name the proposal being weighed and are redrawn on a heartbeat so a long verification does not look like a stalled run. Two extras install the tools Towel uses when they are present: `code-towel[format]` (Black, ruff, isort) formats the code it inserts and sorts inserted imports the way the project does, and `code-towel[types]` (mypy, pyright) annotates generated helpers and type-checks the result. Both behaviors are on by default whenever the tool is installed, and `--no-format` and `--no-types` turn them off:

```bash
pip install "code-towel[format,types]"
```

Platform, CPU, memory, and disk requirements are in [Requirements](#requirements) below.

## Requirements

**Platform.** Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs POSIX filesystem semantics. Parallel analysis uses the `fork` start method; under other start methods analysis runs on a single core. Windows is not a supported or validated release platform.

**CPU.** One core is enough. The tool parallelizes a large analysis by forking one worker per core, which speeds up big projects but changes nothing about the result; `TOWEL_WORKERS=1` keeps it on one core and `TOWEL_WORKERS=N` caps the workers.

**Memory.** Every figure here was measured on an Apple M5 Max (18 cores, 128 GiB); [the known limitations](https://github.com/ericeallen/towel/blob/v1.772/docs/KNOWN_LIMITATIONS.md#measurement-environment) record the rest of the environment. Measured in September 2026: a single analysis process holds the parsed modules and its caches: tens of megabytes for one file, about 250 MB for a 140,000-line project. Forking multiplies that by the worker count, because each worker starts as a copy-on-write fork whose caches then diverge; a 200,000-line project on an 18-core machine peaked near 7.4 GB across 20 processes. The tool estimates the parent's size against physical memory at fork time and caps the workers at roughly a third of RAM, but the estimate is not a guarantee. On a memory-constrained machine, or when running several large refactorings at once, set `TOWEL_WORKERS` low; at `TOWEL_WORKERS=1` the footprint stays at the single-process figure.

**Disk.** Both in-place and out-of-place refactoring need temporary space for staged changes and recovery journals, and both copy the whole project's Python sources, stubs and configuration into a private temporary stage, removed when the run ends, where the refactoring is done. An out-of-place run also copies the target in full, and writes only the refactored target to the output directory; an in-place run writes back only the files it rewrote. Optional type checking adds, for the life of the run, a second copy of the project's checker inputs for pyright and a mypy cache directory; both are removed when the oracle is closed. Recovery journals, `.towel-transaction-<id>` directories at the common root of a batch, hold the original source bytes until you resolve them; a pending journal blocks a later run only when its manifest names a file that run would change.

## How long it takes

There is no time budget; progress is reported per phase on stderr, so stdout can be piped or redirected. Ctrl-C and SIGTERM both end the run cleanly (an interrupted apply is rolled back from its journal), and a reader that closes the pipe early is not an error. Pairing is quadratic in the number of candidate blocks per file, so a few large modules with many near-identical methods are the worst case, not total line count. With N near-identical blocks in one file every pair proposes the same N-site extraction; each distinct proposal is kept once, but evaluating the pairs still grows as N cubed until the first application collapses them into one helper. `--max-pairs` bounds the candidate pairs one analysis evaluates (the largest groups of similar blocks are left out, with a warning) and `--min-lines` raises the smallest block considered.

Rough expectations with the defaults, one core: a 2,000-line module takes
seconds; Towel's own source (about 22,000 lines) runs to a fixed
point in 11.9 s, or 8.4 s without the type checker and formatter (commit
`5ff2458`, September 19, 2026); a package the size of boltons (24,000 lines) or Click (29,000
lines) takes tens of seconds, and pygments (137,000 lines) about two
minutes, by the dated measurements in the
[performance section of Known limitations](https://github.com/ericeallen/towel/blob/v1.772/docs/KNOWN_LIMITATIONS.md#performance),
which is the one table of package timings and says what each figure
measured; the largest projects in the ecosystem check, networkx and Sphinx
(150,000 to 200,000 lines), take several minutes to over half an hour
(Sphinx: 2058 s with the defaults in the ecosystem check, September 2026).

The two largest projects in the ecosystem check, networkx and Sphinx, are the slowest because their directory fixed point re-pairs the project after each batch of applied changes; later global passes re-pair only the files rewritten since the previous one, which changes no proposal (the argument is in [the architecture document](https://github.com/ericeallen/towel/blob/v1.772/docs/ARCHITECTURE.md#incremental-global-passes-and-why-they-are-exact)), and the ecosystem check still gives both extended budgets. Forking cuts the wall time of a large project several-fold on a multi-core machine. With the type checker and formatter installed, the defaults add to an annotated project's time in proportion to the number of applied refactorings, each of which is type-checked: Towel's own source (15 applied, commit `5ff2458`, September 19, 2026) takes 8.4 s with `--no-types --no-format` and 11.9 s with the defaults, one core; that historical implementation held mypy in-process and raised peak memory from about 174 MB to about 894 MB there. The current checker verifies the complete prospective project through an owned mypy worker that forks a child per build, and a long-lived pyright language server over a private copy of the project; those timings do not measure it.

## Use

```bash
# Read-only analysis: lists each opportunity with the extracted helper and,
# per call site, the original block (-) next to the generated call (+)
towel preview path/to/project

# Write to a new output directory
towel dry path/to/project path/to/cleaned --no-interactive

# Explicit in-place refactoring: review through version control afterward
towel dry path/to/project path/to/project --no-interactive

# Bound the number of changes
towel dry example.py cleaned.py --no-interactive --max-refactorings 10

# Leave helpers unannotated, or insert code as rendered without formatting
towel dry path/to/project path/to/cleaned --no-interactive --no-types --no-format

# Also share helpers between duplicates in different modules
towel dry path/to/project path/to/cleaned --no-interactive --cross-module
```

By default Towel extracts only duplicates within one module, and adds no import between the project's modules that runs. `--cross-module` also shares a helper between duplicates in different modules: the helper lives in one module and the others import it, a new dependency between those modules, which is why it has to be asked for. The import is spelled the way the program's own imports show it works wherever the program runs: relatively between modules of one package, and across packages only where the importing package already imports the other; anything else is declined. Before writing anything, a `--cross-module` run names every place where the program's imports do not name its modules unambiguously. It refuses the run when one leaves a name of the code being refactored in doubt: a stale `build/lib` copy beside the package, a name that two directories both provide, or a relative import that climbs out of its package. `--exclude build`, or the directory holding the stray copy, is the remedy. An import of a module the tree lacks refuses nothing. Test data importing a mocked module, or a `_version.py` that installing the project generates, are examples. The file making such an import is left unchanged and named in the report.

A separate output must not already exist or overlap the input. Cancellation leaves the filesystem unchanged. Symlinked Python files are excluded from directory analysis. The API accepts an empty output directory for fixture and integration workflows. Every run, in place or not, refactors a private copy of the whole project and writes nothing until it has succeeded, its final type-check confirmation included; an in-place run then writes every file it changed as one batch. Each file is replaced atomically; caught application failures roll back, and interrupted batches retain a recovery journal. Readers can observe a partially applied batch. Keep exclusive write access to the project and its parent while applying or recovering: snapshot checks detect stale files but cannot prevent a noncooperating editor from writing in the final check/replace interval.

To roll back an interrupted batch, use `towel recover /path/to/.towel-transaction-<id>` (the name the run reported); a path through a symbolic link, such as macOS's `/tmp`, names the same journal. Recovery refuses detected conflicting edits and keeps the journal for resolution, and refuses a journal that is itself a symbolic link, belongs to another user or is not owner-only (mode 0700, as every journal is written), saying which and what to do. Review local journals before recovery; they contain original source bytes. Do not delete a journal before resolving the interrupted operation. A run refactors the target inside a private copy of its whole project, so import cycles and import-time effects through modules outside the target are seen, and the result is written only when the run succeeds: all at once to an output directory, or, in place, as one journaled batch of the files it changed, checked first against what each held when the run began. A failed or interrupted run leaves the project and the output path as they were, and a file edited while the run was working refuses the write rather than lose the edit. A project root holding more than 20,000 Python files is refused rather than copied; give the code its own `pyproject.toml`, `setup.cfg` or `setup.py`, or move it out of the larger tree.

A `dry` run ends by counting what it declined, by reason: the proposals it built and did not apply (refused by the type checker; one of the reasons no helper signature can answer, such as a block that narrows what its caller reads after the call or that declares its class's attributes; not representable in their file's encoding; and so on) and, when it applied nothing, the candidate pairs it declined. For each candidate pair's reason, set `DEBUG_PROPOSAL_REJECTIONS=1`. `TOWEL_DEBUG_TYPES=1` prints what the type checker answered for each probed expression and the errors that make a helper's annotations fall back; `DEBUG_VALIDATION=1` and `DEBUG_OVERLAP_FILTER=1` trace the pair stages and the overlap filter. These, and `TOWEL_WORKERS`, are read once at startup.

Run `towel dry --help` for the cross-module, exclusion, typing, formatting, refactoring-count, block-size, parameter-limit, pair-budget, and progress options. Every boolean option is a `--x/--no-x` pair; the `--help` text names the default.

## What is analyzed

The pipeline parses modules, analyzes scopes, collects functions and classes, compares candidate blocks, and constructs extraction proposals. It supports same-file candidates and, with `--cross-module`, cross-file ones, parameter differences, return propagation, and selected class-method extractions. Directory mode skips hidden directories, `__pycache__`, `venv`, `env`, `node_modules`, any directory holding a `pyvenv.cfg`, the names given by `--exclude`, and symlinked files.

Differing sub-expressions become helper parameters. Literals, names the call site resolves on every path, and tuples of those are passed eagerly; a name the site may not resolve is passed as a thunk. A name that every site resolves at module scope (a class, an import, a helper defined below its callers) is not a parameter of a same-module helper at all: the helper reads it bare, as the block did; a cross-file helper still takes it as a parameter. No helper takes a builtin as a parameter: where a builtin the blocks read may differ between their sites (one function binds `len` and the other reads the builtin, or, across modules, a module may hold the name, as a test's `mock.patch("pkg.mod.len")` makes it), the pair is declined. `--parameterize-builtins` passes such a builtin as an ordinary parameter instead, each site giving its own, so a patch of either module still reaches that module's code; a builtin every site reads alike is still read bare. Any other expression is passed as a zero-argument thunk and evaluated inside the helper where the original expression stood, so evaluation order, count, and conditionality are preserved. Expressions that read names bound inside the block are lambda-lifted with those names as arguments. A thunk the helper would evaluate first, once, and unconditionally is passed eagerly instead, since nothing can observe the difference. Before a proposal is offered, the helper is instantiated with each call's arguments and must reproduce the original block up to renamed binders.

The refactoring pipeline preserves the original Python operators. Generator/suspension operations and frame-sensitive calls such as `locals()` are conservatively rejected. Nested blocks that bind names used outside the block are rejected until full control-flow liveness is supported. Static local import cycles and cross-module global declarations are rejected. This reduces the number of proposals rather than claiming an unsupported transformation is safe.

Dynamic imports, reflection, arbitrary callbacks, runtime rebinding, metaclasses, and external side effects limit what can be established statically. Each engine owns a bounded analysis session with content checks and isolated AST snapshots. The test import-isolation harness and an individual engine instance require sequential use. Candidates involving detected namespace rebinding, frame inspection, or comprehension assignment expressions are rejected; opaque external reflection and rebinding remain outside the supported model.

## Why some arguments are wrapped in `lambda`

When the differing sub-expression between two duplicates is more than a plain value, Towel passes it as a zero-argument lambda (a *thunk*) and calls it inside the helper at the exact place the original expression stood. In the output that looks like this:

```python
notify(lambda: welcome_email(user))
```

To a casual reader the lambda looks redundant. It is not. Passing the expression directly would evaluate it once, unconditionally, at the call site, and that changes behavior whenever the original evaluated it conditionally, more than once, or not at all. Consider two blocks that differ only in one call (illustrative):

```python
# before
if user.active:
    send(welcome_email(user))
```

Pass that call eagerly and it runs for every user, active or not:

```python
# WRONG: welcome_email(user) runs unconditionally, with its side effects, and
# can raise, even when user.active is False
def notify(message, user):
    if user.active:
        send(message)

notify(welcome_email(user), user)
```

The thunk defers it to the spot the original code ran it, preserving the condition:

```python
# correct
def notify(build_message, user):
    if user.active:
        send(build_message())

notify(lambda: welcome_email(user), user)
```

The same reasoning covers an expression inside a loop (the thunk runs once per iteration, as the original did) or one that might raise. Towel keeps the wrapper only where it can matter: an expression the helper would evaluate first, once, and unconditionally is passed directly, because then nothing can observe the difference. So a `lambda:` in the output is a deliberate marker that Towel is preserving that argument's timing, count, or conditionality; its absence means eager evaluation was proven equivalent.

## How helpers get their types

Towel annotates a helper only in code that already uses annotations, from what the call sites declare and what the project's type checker says. It uses mypy or Pyright according to the project's configuration; with both configured, mypy infers and both verify, the first to report a new error settling a candidate so the other is not asked. A checker the project configures but that is not installed where Towel runs refuses the typed run before anything is written, naming the checker: install it, or rerun with `--no-types` to refactor unverified. Only a project that configures no checker is checked by whichever one is installed, mypy first, and mypy then checks it with its defaults, as `mypy` itself would check the same files: the bodies of functions without annotations go unchecked, and an import mypy finds no types for is an error. Only inference looks inside those bodies, where mypy otherwise reveals nothing but `Any`. The project's configured mypy plugins (django-stubs, pydantic, SQLAlchemy and the like) are loaded and run in every check, as in the project's own mypy run, so they execute project-configured code; a plugin that cannot be loaded where Towel runs refuses the typed run before anything is written, quoting mypy's error. A run checked through a language server is confirmed once more at the end by a pyright started from nothing. Before using the checker, Towel checks the original project and says, before it changes anything, what that check reports. The errors it reports are left as they are, and a change is rejected only for an error they do not account for: in a file no change has touched, the same message at the same line; in one a change has touched, the same message on the same line wherever the change moved that line, and on the lines the change wrote, no more often than on the lines it replaced. A message that names a line (`already defined on line 12`) therefore reappears as new when that line moves, and the change is declined rather than an error hidden. Once a change is written, its own check is what the next is compared with, and the final cold check compares the same way. A file where the check leaves a name it cannot type, such as an import it cannot resolve or finds no stubs for, is not changed at all, since `Any` accepts every use of such a name; the run names those files, and installing what they import where Towel runs has them refactored with types. Nor is code the checker does not look at: a module or branch it takes to be unreachable on the platform and Python it checks for (`if sys.platform == "win32":` on Linux, a module that asserts another platform), where it reports nothing. Towel asks every configured checker where it looks, by a `reveal_type` it answers only there, names such regions before the run, and declines any change that writes a line some checker does not look at. Only a checker that cannot run at all (a crash, a timeout, a plugin that cannot be loaded) refuses the typed run; `--no-types` then refactors without verification, leaving existing source annotations intact and generating unannotated helpers.

A parameter whose every argument is an annotated, never-rebound parameter of the enclosing function takes that annotation. For other expressions, Towel asks the checker for their types at the call. When sites disagree, the parameter takes the union of their types, normalized by the checker's subtype relation, so `int | bool` is written `int` and a subclass disappears under its base. The return type must satisfy every site: Towel uses the narrower declared return type, or a revealed type that the checker confirms is a subtype of every declaration. Thunks are `Callable[[], T]`. A reference to a class is written `type[C]`, not the constructor signature a checker reports for it, and a class the module cannot reach is imported under `TYPE_CHECKING` and named directly. A method's receiver is never annotated from its call sites: the class the method is defined on already states its type, which is what a bare `self` means. Once a helper has any other annotation, the rest are completed with `Any`, so no signature is partial. An annotation is written as a string where the helper's module would evaluate syntax the project's oldest Python lacks: checkers spell types as the newest Python does (`int | None`, `list[int]`), so in a module without `from __future__ import annotations` of a project declaring `requires-python = ">=3.9"`, the union is quoted and the subscripted `list` is not; a project that declares no Python gets both quoted unless its module already uses them.

Towel also preserves relationships through type anti-unification, rather than widening a disagreement to `Any`. For example, helpers shared by `list[int] -> int` and `list[str] -> str` can use `list[T] -> T`; integer and string addition can use one constrained type parameter for both operands and the result. Existing generic callers receive fresh helper type parameters with supported bounds or constraints preserved. Instance and class helper methods retain type parameters already bound by their host class; static helpers infer their parameters from the explicit arguments. [The type-parameter design](https://github.com/ericeallen/towel/blob/v1.772/docs/proposals/type-parameters.md) records the inference order and the cases it declines.

When verification is enabled, all prospective changed modules are checked together with unchanged consumers. Towel tries a precise ordinary signature first, then generic signatures, then the `Any` fallback, and the unannotated one only when every error of the `Any` refusal lies inside the helper itself, an error anywhere else surviving the change; each variant must pass. Text identical to what a file already holds is withheld from mypy so its incremental cache applies. Checker failure or remaining errors decline the proposal. Without a checker, Towel copies and does not reason: unions are unreduced and disagreeing declarations leave the return bare. [The architecture document](https://github.com/ericeallen/towel/blob/v1.772/docs/ARCHITECTURE.md#helper-annotations) describes the published annotation rules; the type-parameter design records the generic inference and its limits.

## Naming the helpers with an LLM

Towel deliberately generates meaningless names, `__extracted_func_3` and `__param_0`, and leaves the naming to a separate, review-first step. Extraction is a verified mechanical transformation; choosing a good name is a judgment call, so the two are kept apart. The intended workflow hands the naming to a coding assistant, because the assistant reads far better names out of the call sites than any heuristic, and you review its choices before they touch the code.

The round trip is: extract, export an inventory, let the LLM propose names, apply them as one checked batch, and re-run your tests.

```bash
# 1. Extract with placeholder names, then export the inventory the LLM reads.
towel dry path/to/project path/to/cleaned --no-interactive
towel rename-helpers path/to/cleaned --list --json > helpers.json

# 2. Have the assistant read helpers.json and write renames.json:
#    a mapping from each inventory key to the name it chose.

# 3. Preview the batch, then apply it, then re-run your tests.
towel rename-helpers path/to/cleaned --rename-file renames.json --preview
towel rename-helpers path/to/cleaned --rename-file renames.json
```

The JSON inventory gives the assistant what it needs to name well: every helper with its scope, source, and call sites, and for each parameter its evaluation kind (`value`, `thunk`, `lifted`, `receiver`) and the actual argument expressions passed at every call site. A parameter that always receives `user.email` and `account.email` should become `email`, and the argument expressions are how the assistant sees that.

Each helper also carries a `changes` list: for every call site of a generated helper, the exact original block it replaced (`before`) next to the generated call (`after`); sites redirected to an existing function are not listed, since no helper was inserted for them. Seeing what the code did before extraction is what lets an assistant finish good names, write a docstring, and infer parameter and return types. This comes from a small `.towel-helpers.json` that `towel dry` writes next to its output; it is only for the naming step and is safe to delete afterward.

Each inventory entry carries the exact mapping key to use as a rename target: `"path.py:helper"` renames a module-level helper together with its importers, `"path.py:Class.__helper"` renames a class-private method helper together with its references in that class's body, and `"path.py:helper.__param_0"` (or `"path.py:Class.__helper.__param_0"`) renames a parameter within the helper's scope. A method helper is class-private, `__extracted_func_0`, so that no subclass can override it, and its new name must be class-private too: it starts with two underscores and does not end with two, or the batch is refused. An older class-level helper without the leading double underscore is still renamed by its bare name, `"helper"`, together with every attribute reference. The rename is applied as one atomic batch with scope and importer checks; a name collision, a class-private helper spelled out elsewhere (`obj._Class__helper`), or a dynamic reference aborts the whole batch and reports the reason, so a bad suggestion changes nothing. Rename after adopting an out-of-place output into its project: the output directory is named for the output, not the package, so an import naming the package cannot be matched there, and the batch is refused. `--preview` reports what would change without writing,
as the same JSON when `--json` is given. `--file` and `--function` (each repeatable) limit
the inventory to particular modules or helpers, and `--llm claude|gpt|copilot|generic`
phrases the interactive prompt for a particular assistant.

The shared `towel-rename` skill (in the agent-skills repository) walks an assistant through the whole loop: extract, review the diff, name, apply, and re-test. An interactive prompt mode is also available for naming by hand, and it calls no LLM service.

## Development and verification

Use Python 3.13 for the shared formatting and typing gates. The lockfile also resolves test dependencies for the supported older interpreters. Python 3.11 is the minimum supported interpreter.

```bash
uv sync --frozen --extra dev
uv run --frozen black --check src/towel tests scripts
uv run --frozen flake8 src/towel scripts tests
uv run --frozen mypy
uv run --frozen coverage run -m pytest -q
uv run --frozen coverage combine
uv run --frozen coverage report --fail-under=85
uv run --frozen bandit -r src/towel scripts -ll
just audit-dependencies
uv run --frozen python -m build
```

Alternatively, create `.venv`, activate it, and install `pip install -e '.[dev]'`. `just check` checks formatting, lint, typing, and Bandit; `just test` runs the tests. These commands propagate failures. The pre-commit hooks call `python` from the environment, so activate it (or prefix `PATH="$PWD/.venv/bin:$PATH"`) before installing or running them; the mypy hook checks the whole tree, so files in progress must type-check too. The tests for formatting and typing need the `dev` extra's Black, ruff, isort, mypy, and pyright; the mypy and pyright tests skip when those are absent.

`towel dry PKG PKG --exclude tests` leaves a directory name out of directory mode (repeatable, and accepted by `preview` too); use it for packages that carry their test suite inside themselves, and for a stray copy of a package that makes its imports ambiguous: the program's import model reads nothing there either. Large analyses fork worker processes after parsing when a timed probe projects enough work; set `TOWEL_WORKERS=1` to stay on one core or another value to cap the workers. Set `TOWEL_CHECK_AST_IMMUTABLE=1` to verify on every cache reuse that analysis left the module's AST untouched.

`just ecosystem --run-untrusted-code --no-types` runs the standing ecosystem
check (`scripts/ecosystem_check.py`). It tests each of the 141 entries in
`scripts/ecosystem/manifest.toml` before and after refactoring a copy. Revisions
are pinned except for the deliberate check of Towel's current `main`, whose
resolved commit is recorded. Weekly CI uses this explicit `--no-types` mode
because the manifest supplies runtime test dependencies, not each project's
complete typing environment. Omitting `--no-types` retains Towel's default
type-checking policy; the harness never disables checking automatically.

The Markdown and JSON reports record the requested typing mode and each
project's outcome. A complete gate accepts only `PASS`, `NO_CHANGE`, and
specifically documented `BROKEN_KNOWN` outcomes; setup, typing, timeout, and
incomplete test-run failures fail the gate. A no-change result does not validate
a transformation. Default typing has separate integration coverage, described
with the corpus results in [Production readiness](https://github.com/ericeallen/towel/blob/v1.772/docs/PRODUCTION_READINESS.md).

The check executes third-party code with your privileges, so it requires
`--run-untrusted-code` (or `TOWEL_ECOSYSTEM_RUN_UNTRUSTED=1`) and belongs on a
disposable machine or container. It runs Towel as a user does, inside each
project's own environment, where the project is installed from the tree under
test beside Towel's `format` and `types` extras and what the project declares
its own type check needs: one wheel of `--towel-src`, built when the run starts
or named with `--towel-wheel`, and refused if its code differs from that source. Point `--towel-src` at a committed snapshot's `src` directory,
since the report names its commit. The harness reports the actual source
revision and dirty state; source archives require an independently retained
source manifest.

Behavioral tests compare sampled return values and types, exceptions, output, and argument mutations. Cross-file tests isolate imports for each execution. Empty selections, unsupported class construction, and cross-file returned closures do not count as success. Single-file callable comparison samples one returned-callable layer; deeper returned callables are not validated. These checks are regression evidence, not proof of equivalence for arbitrary programs.

## Documentation

Start here:

- [Quick start](https://github.com/ericeallen/towel/blob/v1.772/docs/QUICKSTART.md) — install and refactor in four steps
- [Known limitations](https://github.com/ericeallen/towel/blob/v1.772/docs/KNOWN_LIMITATIONS.md) — what is verified, what is rejected, and what is outside the model
- [Python API guide](https://github.com/ericeallen/towel/blob/v1.772/docs/USAGE_GUIDE.md) — using `UnificationRefactorEngine` directly

How it works and why to trust it:

- [Architecture](https://github.com/ericeallen/towel/blob/v1.772/docs/ARCHITECTURE.md) — the pipeline, the algorithms, and their references
- [Production readiness](https://github.com/ericeallen/towel/blob/v1.772/docs/PRODUCTION_READINESS.md) — the ecosystem evidence behind the claims
- [Adversarial review](https://github.com/ericeallen/towel/blob/v1.772/docs/ADVERSARIAL_REVIEW.md) — defects found and repaired

Project:

- [Changelog](https://github.com/ericeallen/towel/blob/v1.772/CHANGELOG.md) — changes by release, including 1.772
- [Release log](https://github.com/ericeallen/towel/blob/v1.772/docs/RELEASE_LOG.md) — engineering checkpoints and validation history
- [Contributing](https://github.com/ericeallen/towel/blob/v1.772/CONTRIBUTING.md) · [Security policy](https://github.com/ericeallen/towel/blob/v1.772/SECURITY.md) · [Releasing](https://github.com/ericeallen/towel/blob/v1.772/docs/RELEASING.md)

## License

The repository declares the [Apache License 2.0](https://github.com/ericeallen/towel/blob/v1.772/LICENSE), with Eric Allen copyright headers. The audit preserves that declaration. No release or remote publication is performed by the audit workflow.
