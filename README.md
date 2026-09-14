# Towel

Towel finds repeated Python code using unification and proposes helper-function extractions.

**Release status: 1.1.0.** Every accepted proposal is verified by instantiating the helper with each call's arguments and comparing the result with the block it replaces; arguments that could have observable effects or fresh identity are evaluated inside the helper at their original position. Seven public projects pass their full test suites before and after transformation under the defaults. Refactoring is still a change to your code: preview first, review the diff, and run your tests. [Known limitations](docs/KNOWN_LIMITATIONS.md) lists what is verified, what is rejected, and what remains outside the model; [the readiness report](docs/PRODUCTION_READINESS.md) records the evidence.

## Install

Python 3.11 is the minimum. The test matrix covers Python 3.11–3.13. Local verification runs on macOS; CI is configured for Linux. Filesystem application requires POSIX semantics. The runtime uses the standard library. Optional `tqdm` provides progress bars.

```bash
python -m pip install .
towel --version
towel --help
```

The commands below describe this checkout; previously published distributions may differ.

## Use

```bash
# Read-only analysis
towel preview path/to/project

# Write to a new output directory
towel dry path/to/project path/to/cleaned --non-interactive

# Explicit in-place refactoring: review through version control afterward
towel dry path/to/project path/to/project --non-interactive

# Bound the number of changes
towel dry example.py cleaned.py --non-interactive --max-iterations 10
```

A separate output must not already exist or overlap the input. Cancellation leaves the filesystem unchanged. Symlinked Python files are excluded from directory analysis. The API accepts an empty output directory for fixture and integration workflows. Complete changes are staged and checked before the first write. Each file is replaced atomically; caught application failures roll back, and interrupted batches retain a recovery journal. Readers can observe a partially applied batch. Keep exclusive write access to the project and its parent while applying or recovering: snapshot checks detect stale files but cannot prevent a noncooperating editor from writing in the final check/replace interval.

To roll back an interrupted batch, use `towel recover /path/to/.towel-transaction-active`. Recovery refuses detected conflicting edits and keeps the journal for resolution. Review local journals before recovery; they contain original source bytes. Do not delete a journal before resolving the interrupted operation. Initial out-of-place copying is staged separately so copy errors do not leave a partial output.

For detailed conservative rejection reasons, set `DEBUG_PROPOSAL_REJECTIONS=1` when running preview.

`towel-dry` and `towel-preview` are compatibility entry points. `python scripts/dry` delegates to the same installed CLI. Run `towel dry --help` for import-layout, iteration, and progress options.

## What is analyzed

The pipeline parses modules, analyzes scopes, collects functions and classes, compares candidate blocks, and constructs extraction proposals. It supports same-file and cross-file candidates, parameter differences, return propagation, and selected class-method extractions.

Differing sub-expressions become helper parameters. Names, literals, and tuples of those are passed eagerly; any other expression is passed as a zero-argument thunk and evaluated inside the helper where the original expression stood, so evaluation order, count, and conditionality are preserved. Expressions that read names bound inside the block are lambda-lifted with those names as arguments. A thunk the helper would evaluate first, once, and unconditionally is passed eagerly instead, since nothing can observe the difference. Before a proposal is offered, the helper is instantiated with each call's arguments and must reproduce the original block up to renamed binders.

The refactoring pipeline preserves the original Python operators. The legacy `ast_normalizer` utilities remain available with deprecation warnings for compatibility, but can change Python behavior and are not used by this pipeline. Generator/suspension operations and frame-sensitive calls such as `locals()` are conservatively rejected. Nested blocks that bind names used outside the block are rejected until full control-flow liveness is supported. Static local import cycles and cross-module global declarations are rejected. This reduces the number of proposals rather than claiming an unsupported transformation is safe.

Dynamic imports, reflection, arbitrary callbacks, runtime rebinding, metaclasses, and external side effects limit what can be established statically. Each engine owns a bounded analysis session with content checks and isolated AST snapshots. The test import-isolation harness and an individual engine instance require sequential use. Candidates involving detected namespace rebinding, frame inspection, or comprehension assignment expressions are rejected; opaque external reflection and rebinding remain outside the supported model.

## Helper names

Generated names are deliberately meaningless: `__extracted_func_3`, `__param_0`. Naming them is a separate step designed to be driven by a coding assistant.

```bash
towel rename-helpers path/to/cleaned --list --json > helpers.json
towel rename-helpers path/to/cleaned --rename-file renames.json --dry-run
towel rename-helpers path/to/cleaned --rename-file renames.json
```

The JSON inventory lists every helper with its scope, source, call sites, and parameters. Each parameter carries its evaluation kind (`value`, `thunk`, `lifted`, `receiver`) and the argument expressions bound to it at every call site, which is what a good name is derived from. Each entry also carries the exact mapping keys: `"path.py:helper"` renames a module-level helper together with its importers, `"helper"` renames a unique class-level helper together with every attribute reference, and `"path.py:helper.__param_0"` renames a parameter within the helper's scope. A mapping is applied as one batch; a collision, a mangled name, or a dynamic reference aborts the whole batch with the reason.

The shared `towel-rename` skill (in the agent-skills repository) walks an assistant through extract, review, name, and re-test. The interactive prompt mode remains available and does not call any LLM service.

## Development and verification

Use Python 3.13 for the shared formatting and typing gates. The lockfile also resolves test dependencies for the supported older interpreters. Python 3.11 is the minimum supported interpreter.

```bash
uv sync --frozen --extra dev
uv run --frozen black --check src/towel tests scripts
uv run --frozen flake8 src/towel scripts
uv run --frozen mypy
uv run --frozen coverage run -m pytest -q
uv run --frozen coverage report --fail-under=85
uv run --frozen bandit -r src/towel -ll
uv run --frozen python -m build
```

Alternatively, create `.venv`, activate it, and install `pip install -e '.[dev]'`. `just check` checks formatting, lint, and typing; `just test` runs the tests. `just ci` runs both. These commands propagate failures. Activate the environment before installing/running pre-commit hooks so they use the same toolchain.

`towel dry PKG PKG --exclude tests` leaves a directory name out of directory mode (repeatable); use it for packages that carry their test suite inside themselves. Large analyses fork worker processes after parsing when a timed probe projects enough work; set `TOWEL_WORKERS=1` to stay on one core or another value to cap the workers. Set `TOWEL_CHECK_AST_IMMUTABLE=1` to verify on every cache reuse that analysis left the module's AST untouched.

`just ecosystem` runs the standing ecosystem check (`scripts/ecosystem_check.py`): it clones the public projects listed in `scripts/ecosystem/manifest.toml`, runs each project's own test suite, refactors a copy with the CLI defaults, runs the suite again, and reports `PASS`, `NO_CHANGE`, `BROKEN`, `CRASH`, `TIMEOUT`, `UNSUPPORTED`, or a documented `BROKEN_KNOWN` per project, with a Markdown and JSON report. It runs weekly and on demand in CI and is the evidence behind [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md).

Behavioral tests compare sampled return values and types, exceptions, output, and argument mutations. Cross-file tests isolate imports for each execution. Empty selections, unsupported class construction, and cross-file returned closures do not count as success. Single-file callable comparison samples one returned-callable layer; deeper returned callables are not validated. These checks are regression evidence, not proof of equivalence for arbitrary programs.

## Documentation

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Production readiness and ecosystem evidence](docs/PRODUCTION_READINESS.md)
- [Adversarial review](docs/ADVERSARIAL_REVIEW.md)
- [Historical issues](KNOWN_ISSUES.md)

Historical notes and example outputs document earlier versions and may describe behavior superseded by the current audit. The current CLI help and source define the available interface.

## License

The repository declares the [Apache License 2.0](LICENSE), with Eric Allen copyright headers. The audit preserves that declaration. No release or remote publication is performed by the audit workflow.
