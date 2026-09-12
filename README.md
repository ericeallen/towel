# Towel

Towel finds repeated Python code using unification and proposes helper-function extractions.

**Release status: experimental.** Refactoring Python can change behavior in ways that static analysis and sampled tests do not detect. Preview first, review the diff, and run the affected project's tests before adopting generated code. See [the release audit](docs/OPEN_SOURCE_AUDIT.md) for verified results and remaining limitations. Existing version metadata is retained until a maintainer chooses the next release.

## Install

Python 3.10 or later is supported. Python 3.11+ uses the standard library; Python 3.10 additionally installs `tomli` to read project layouts. Optional `tqdm` provides progress bars.

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

A separate output must not already exist or overlap the input. Cancellation leaves the filesystem unchanged. Symlinked Python files are excluded from directory analysis. The API accepts an empty output directory for fixture and integration workflows. Multi-file writes are validated for Python syntax before the first write, but are not a transactional filesystem operation.

`towel-dry` and `towel-preview` are compatibility entry points. `python scripts/dry` delegates to the same installed CLI. Run `towel dry --help` for import-layout, iteration, and progress options.

## What is analyzed

The pipeline parses modules, analyzes scopes, collects functions and classes, compares candidate blocks, and constructs extraction proposals. It supports same-file and cross-file candidates, parameter differences, return propagation, and selected class-method extractions.

The refactoring pipeline preserves the original Python operators. The legacy `ast_normalizer` utilities remain available for compatibility, but can change Python behavior and are not used by this pipeline. Generator/suspension operations and frame-sensitive calls such as `locals()` are conservatively rejected. Nested blocks that bind names used outside the block are rejected until full control-flow liveness is supported. Static local import cycles and cross-module global declarations are rejected. This reduces the number of proposals rather than claiming an unsupported transformation is safe.

Dynamic imports, reflection, arbitrary callbacks, runtime rebinding, metaclasses, and external side effects limit what can be established statically. The analysis cache checks source contents between calls. Analysis and the test import-isolation harness are not designed for concurrent threads sharing interpreter state.

## Helper names

```bash
towel rename-helpers path/to/cleaned --dry-run
```

The command prints a prompt for manual use with an assistant; it does not call an LLM service. JSON mappings can be applied with `--rename-file`. Renaming replaces identifier tokens, preserving comments and strings, and rejects keywords and collisions within a changed file. It is not a complete symbol-resolution engine: review mappings across scopes and modules, especially file-qualified selections and reflective string references.

## Development and verification

Use Python 3.13 for the shared formatting and typing gates. The lockfile also resolves test dependencies for the supported older interpreters. The minimum is Python 3.10 so the development toolchain can use patched releases.

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

Behavioral tests compare sampled return values and types, exceptions, output, and argument mutations. Cross-file tests isolate imports for each execution. Empty selections, unsupported class construction, and cross-file returned closures do not count as success. Single-file callable comparison samples one returned-callable layer; deeper returned callables are not validated. These checks are regression evidence, not proof of equivalence for arbitrary programs.

## Documentation

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Historical issues](KNOWN_ISSUES.md)

Historical notes and example outputs document earlier versions and may describe behavior superseded by the current audit. The current CLI help and source define the available interface.

## License

The repository declares the [Apache License 2.0](LICENSE), with Eric Allen copyright headers. The audit preserves that declaration. No release or remote publication is performed by the audit workflow.
