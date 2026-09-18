# Towel

Towel finds repeated Python code using unification and proposes helper-function extractions.

> **Install from PyPI as [`code-towel`](https://pypi.org/project/code-towel/)** (the command is `towel`):
> `pip install code-towel`
> Do **not** install `towel`: `pip install towel` and `uvx towel` fetch a different, unrelated project.

**Release status: 1.618 (beta).** Every accepted proposal is verified by instantiating the helper with each call's arguments and comparing the result with the block it replaces; arguments that could have observable effects or fresh identity are evaluated inside the helper at their original position. A standing ecosystem check refactors 141 public projects, among them Towel's own releases, and runs each one's own test suite before and after: 117 pass identically, 20 produce no proposal, and 4 differ only in documented frame-, line-, or source-sensitive ways (see below). Refactoring is still a change to your code: preview first, review the diff, and run your tests. [Known limitations](docs/KNOWN_LIMITATIONS.md) lists what is verified, what is rejected, and what remains outside the model; [the readiness report](docs/PRODUCTION_READINESS.md) records the evidence.

**New here?** The [Quick start](docs/QUICKSTART.md) gets you from install to a reviewed refactoring in four steps.

## What it does

Towel finds code that is repeated across your functions and pulls each group of
duplicates into one shared helper, rewriting the copies as calls to it. It works
by *anti-unification*: it computes the least-general generalization of the
matching blocks, so the parts that are the same become the helper's body and the
parts that differ become its parameters.

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
rename afterward) and rewrites both functions to call it. The inserted code is
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

When a duplicate is the whole body of an existing function, Towel does not
extract a helper that would only restate it: the function is kept and the other
copies call it, so two identical functions become one function and one
one-line forwarder.

What makes Towel different from a search-and-replace is that it is conservative
and verified. It proposes an extraction only when it can prove the result runs
the same as the original — instantiating the helper with each call's arguments
and comparing against the block it replaced — and it refuses cases it cannot
establish rather than guess. It skips trivial extractions that would add
indirection without sharing real logic, wraps arguments that must not be
evaluated eagerly in zero-argument `lambda`s (see
[below](#why-some-arguments-are-wrapped-in-lambda)), and leaves naming to you.
Nothing is written without your say-so: the workflow is preview, refactor into a
copy, review the diff, and run your tests.

## Install

The PyPI package is **`code-towel`**; installing it gives you the **`towel`** command.

```bash
pip install code-towel
towel --version
towel --help
```

Install `code-towel`, not `towel`: the name `towel` on PyPI is a different, unrelated project, so `pip install towel` and `uvx towel` will not install this tool. To run it with `uvx` without installing, name the package explicitly:

```bash
uvx --from code-towel towel --help
```

The runtime uses only the standard library; optional `tqdm` provides progress bars. Two extras install the tools Towel uses when they are present: `code-towel[format]` (Black, ruff, isort) formats the code it inserts and sorts inserted imports the way the project does, and `code-towel[types]` (mypy, pyright) annotates generated helpers and type-checks the result. Both behaviors are on by default whenever the tool is installed, and `--no-format` and `--no-types` turn them off:

```bash
pip install "code-towel[format,types]"
```

Platform, CPU, memory, and disk requirements are in [Requirements](#requirements) below.

## Requirements

**Platform.** Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs POSIX filesystem semantics. Parallel analysis uses the `fork` start method, so on Windows, or under any start method that is not `fork`, the tool runs on a single core; everything else works.

**CPU.** One core is enough. The tool parallelizes a large analysis by forking one worker per core, which speeds up big projects but changes nothing about the result; `TOWEL_WORKERS=1` keeps it on one core and `TOWEL_WORKERS=N` caps the workers.

**Memory.** A single analysis process holds the parsed modules and its caches: tens of megabytes for one file, about 250 MB for a 140,000-line project. Forking multiplies that by the worker count, because each worker starts as a copy-on-write fork whose caches then diverge; a 200,000-line project on an 18-core machine peaked near 7.4 GB across 20 processes. The tool estimates the parent's size against physical memory at fork time and caps the workers at roughly a third of RAM, but the estimate is not a guarantee. On a memory-constrained machine, or when running several large refactorings at once, set `TOWEL_WORKERS` low; at `TOWEL_WORKERS=1` the footprint stays at the single-process figure.

**Disk.** In-place refactoring needs no extra space. Out-of-place refactoring first copies the whole project to the output directory. Recovery journals under `.towel-transaction-active` hold the original source bytes of a batch until you resolve it.

## How long it takes

There is no time budget; progress is reported per phase and Ctrl-C leaves the files unchanged. Cost is roughly quadratic in the number of similar candidate blocks per file, so a few large modules with many near-identical methods are the worst case, not total line count.

Rough expectations with the defaults:

| Scale | Example | Time |
|---|---|---|
| One module | a 2,000-line file | seconds |
| Small package | boltons, 24,000 lines | about 5 s |
| Medium package | Click, 29,000 lines | about 12 s |
| Towel's own source | 21,000 lines (16,600 of code), fixed point, September 2026 | about 9 s (5.6 s without the type checker and formatter) |
| Large package | pygments, 137,000 lines | tens of seconds |
| Largest in the corpus | networkx and Sphinx, 150,000 to 200,000 lines | several minutes to about half an hour |

The two largest projects in the ecosystem check, networkx and Sphinx, are the slowest because their directory fixed point re-pairs the project after each batch of applied changes; later global passes re-pair only the files rewritten since the previous one, which changes no proposal (the argument is in [the architecture document](docs/ARCHITECTURE.md#incremental-global-passes-and-why-they-are-exact)), and the ecosystem check still gives both extended budgets. Forking cuts the wall time of a large project several-fold on a multi-core machine. With the type checker and formatter installed, the defaults add to an annotated project's time in proportion to the number of applied refactorings, each of which is type-checked: Towel's own source (18 applied) takes 5.6 s with `--no-types --no-format` and 9.0 s with the defaults, one core; the type checker also holds mypy in-process, which raises peak memory from about 160 MB to about 900 MB there.

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
```

A separate output must not already exist or overlap the input. Cancellation leaves the filesystem unchanged. Symlinked Python files are excluded from directory analysis. The API accepts an empty output directory for fixture and integration workflows. Complete changes are staged and checked before the first write. Each file is replaced atomically; caught application failures roll back, and interrupted batches retain a recovery journal. Readers can observe a partially applied batch. Keep exclusive write access to the project and its parent while applying or recovering: snapshot checks detect stale files but cannot prevent a noncooperating editor from writing in the final check/replace interval.

To roll back an interrupted batch, use `towel recover /path/to/.towel-transaction-active`. Recovery refuses detected conflicting edits and keeps the journal for resolution. Review local journals before recovery; they contain original source bytes. Do not delete a journal before resolving the interrupted operation. Initial out-of-place copying is staged separately so copy errors do not leave a partial output.

For detailed conservative rejection reasons, set `DEBUG_PROPOSAL_REJECTIONS=1` when running preview. `TOWEL_DEBUG_TYPES=1` prints what the type checker answered for each probed expression and the errors that make a helper's annotations fall back.

Run `towel dry --help` for the import-layout, typing, formatting, iteration, and progress options. Every boolean option is a `--x/--no-x` pair; the `--help` text names the default.

## What is analyzed

The pipeline parses modules, analyzes scopes, collects functions and classes, compares candidate blocks, and constructs extraction proposals. It supports same-file and cross-file candidates, parameter differences, return propagation, and selected class-method extractions.

Differing sub-expressions become helper parameters. Names, literals, and tuples of those are passed eagerly; any other expression is passed as a zero-argument thunk and evaluated inside the helper where the original expression stood, so evaluation order, count, and conditionality are preserved. Expressions that read names bound inside the block are lambda-lifted with those names as arguments. A thunk the helper would evaluate first, once, and unconditionally is passed eagerly instead, since nothing can observe the difference. Before a proposal is offered, the helper is instantiated with each call's arguments and must reproduce the original block up to renamed binders.

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

Towel annotates a helper only in code that already uses annotations, and only from evidence: what the call sites declare, and what the project's own type checker says. A parameter whose every argument is an annotated, never-rebound parameter of the enclosing function takes that annotation. For an argument that is an expression, Towel asks the checker (mypy, or pyright when that is what the project configures) for the expression's type at the call. When sites disagree, the parameter takes the union of their types, normalized by the checker's own subtype relation, so `int | bool` is written `int` and a subclass disappears under its base. The return type must satisfy every site: when the enclosing functions declare return types, the helper returns the narrower declaration, or the type the checker reveals when the checker confirms it is a subtype of every declaration. Thunks are `Callable[[], T]`. Once a helper has one annotation, the rest are completed with `Any`, so no signature is partial. The annotated helper and its call sites are then type-checked, and if the change introduces an error the annotations fall back to `Any`, and then to none. A class defined in the same module is named bare, by placing the helper after that class when nothing before it runs code at import time; otherwise the name is quoted. Without a checker Towel copies and does not reason: unions are written unreduced and disagreeing declarations leave the return bare. [The architecture document](docs/ARCHITECTURE.md#helper-annotations) states the rules.

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

Each inventory entry carries the exact mapping key to use as a rename target: `"path.py:helper"` renames a module-level helper together with its importers, `"helper"` renames a unique class-level helper together with every attribute reference, and `"path.py:helper.__param_0"` renames a parameter within the helper's scope. The rename is applied as one atomic batch with scope and importer checks; a name collision, a mangled name, or a dynamic reference aborts the whole batch and reports the reason, so a bad suggestion changes nothing. `--preview` reports what would change without writing,
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
uv run --frozen coverage report --fail-under=85
uv run --frozen bandit -r src/towel scripts -ll
uv run --frozen pip-audit --strict
uv run --frozen python -m build
```

Alternatively, create `.venv`, activate it, and install `pip install -e '.[dev]'`. `just check` checks formatting, lint, typing, and Bandit; `just test` runs the tests. These commands propagate failures. The pre-commit hooks call `python` from the environment, so activate it (or prefix `PATH="$PWD/.venv/bin:$PATH"`) before installing or running them; the mypy hook checks the whole tree, so files in progress must type-check too. The tests for formatting and typing need the `dev` extra's Black, ruff, isort, mypy, and pyright; the mypy and pyright tests skip when those are absent.

`towel dry PKG PKG --exclude tests` leaves a directory name out of directory mode (repeatable); use it for packages that carry their test suite inside themselves. Large analyses fork worker processes after parsing when a timed probe projects enough work; set `TOWEL_WORKERS=1` to stay on one core or another value to cap the workers. Set `TOWEL_CHECK_AST_IMMUTABLE=1` to verify on every cache reuse that analysis left the module's AST untouched.

`just ecosystem --run-untrusted-code` runs the standing ecosystem check (`scripts/ecosystem_check.py`): it clones the 141 public projects listed in `scripts/ecosystem/manifest.toml`, each pinned to a reviewed commit, among them Towel's own releases and current `main`, runs each project's own test suite, refactors a copy with the CLI defaults, runs the suite again, and reports `PASS`, `NO_CHANGE`, `BROKEN`, `CRASH`, `TIMEOUT`, `UNSUPPORTED`, or a documented `BROKEN_KNOWN` per project, with a Markdown and JSON report. The check executes those projects' code with your privileges, so the script refuses to run without the `--run-untrusted-code` flag (or `TOWEL_ECOSYSTEM_RUN_UNTRUSTED=1`) and belongs on a disposable machine or container. It runs weekly and on demand in CI, where the runner is discarded afterward, and is the evidence behind [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md). The harness imports Towel from `--towel-src` (this checkout's `src` by default) for the whole run, so a commit or stash made while it runs changes what later projects are tested with; it warns when the tree is dirty. To keep editing during a run, point `--towel-src` at a detached worktree of the commit under test.

Behavioral tests compare sampled return values and types, exceptions, output, and argument mutations. Cross-file tests isolate imports for each execution. Empty selections, unsupported class construction, and cross-file returned closures do not count as success. Single-file callable comparison samples one returned-callable layer; deeper returned callables are not validated. These checks are regression evidence, not proof of equivalence for arbitrary programs.

## Documentation

Start here:

- [Quick start](docs/QUICKSTART.md) — install and refactor in four steps
- [Known limitations](docs/KNOWN_LIMITATIONS.md) — what is verified, what is rejected, and what is outside the model
- [Python API guide](docs/USAGE_GUIDE.md) — using `UnificationRefactorEngine` directly

How it works and why to trust it:

- [Architecture](docs/ARCHITECTURE.md) — the pipeline, the algorithms, and their references
- [Production readiness](docs/PRODUCTION_READINESS.md) — the ecosystem evidence behind the claims
- [Adversarial review](docs/ADVERSARIAL_REVIEW.md) — defects found and repaired

Project:

- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Releasing](docs/RELEASING.md)

## License

The repository declares the [Apache License 2.0](LICENSE), with Eric Allen copyright headers. The audit preserves that declaration. No release or remote publication is performed by the audit workflow.
