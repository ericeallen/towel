# Towel — command runner
#
# Recipes use `uv run --frozen`, matching the README, CONTRIBUTING, and CI, so
# `just` and CI run the same commands. Run `just` or `just --list` for the list.

# Show the available recipes
default:
    @just --list

# === Setup ===

# Install the runtime package into a local .venv (no dev tools)
install:
    uv venv
    uv pip install -e .

# Sync the pinned dev environment (test, lint, type, build tools)
install-dev:
    uv sync --frozen --extra dev

# === Use ===

# Refactor a file or directory into OUTPUT (see `towel dry --help` for ARGS)
dry INPUT OUTPUT +ARGS="":
    uv run --frozen towel dry {{INPUT}} {{OUTPUT}} {{ARGS}}

# Preview refactoring opportunities read-only
preview TARGET:
    uv run --frozen towel preview {{TARGET}}

# Export the helper inventory / apply a rename batch (see `towel rename-helpers --help`)
rename-helpers TARGET +ARGS="":
    uv run --frozen towel rename-helpers {{TARGET}} {{ARGS}}

# Promote helper stubs (defaults to tmp_out_dry_src -> src)
promote-helpers ARGS="":
    uv run --frozen python scripts/promote_dry_helpers.py {{ARGS}}

# === Test ===

# Run the full test suite (the same command CI runs)
test:
    uv run --frozen pytest -q

# Run the suite under coverage and enforce the 85% gate
coverage:
    uv run --frozen coverage run -m pytest -q
    uv run --frozen coverage combine
    uv run --frozen coverage report --fail-under=85

# Write an HTML coverage report to htmlcov/index.html
coverage-html:
    uv run --frozen coverage run -m pytest -q
    uv run --frozen coverage combine
    uv run --frozen coverage html
    @echo "Report at htmlcov/index.html"

# Coverage for the active unification package only
coverage-unification:
    uv run --frozen coverage run -m pytest -q
    uv run --frozen coverage combine
    uv run --frozen coverage report --include="src/towel/unification/*"

# Fast subset: signature gate, pairing, unifier, extractor, regression stability
test-smoke:
    uv run --frozen pytest -q tests/test_signature_prefilter.py tests/test_candidate_index.py \
        tests/test_regression.py tests/test_observational_equivalence.py \
        tests/test_refactor_engine_comprehensive.py tests/test_unifier_core.py \
        tests/test_extractor_comprehensive.py

# Cross-file observational-equivalence harness only
test-crossfile:
    uv run --frozen python -m pytest -q tests/test_crossfile_observational_equivalence.py

# A release-candidate step (docs/RELEASING.md). SEED is a random start, printed, when
# omitted; each case runs in the default and the --cross-module modes, every 20th also
# typed. Each failure is written as a hostile fixture under the directory the run prints,
# and the run exits 1 if any case failed. Extra ARGS go to
# `python -m tests.differential.fuzz` (--jobs, --out, --prefix, --modes, ...).
# Differential fuzzing over N generated cases from seed SEED, failures written as fixtures
fuzz N="2000" SEED="" *ARGS:
    uv run --frozen python -m tests.differential.fuzz --count {{N}} --seed "{{SEED}}" {{ARGS}}

# Observational equivalence for one example file
test-file FILENAME:
    uv run --frozen python -c "from tests.automatic_equivalence_tester import AutomaticEquivalenceTester; from towel.unification.refactor_engine import UnificationRefactorEngine; t = AutomaticEquivalenceTester(UnificationRefactorEngine(max_parameters=5, min_lines=4)); p, f, e = t.test_file('{{FILENAME}}'); print(f'{p}/{p+f} passed'); [print(' ', x) for x in e[:5]]; raise SystemExit(1 if f or not p else 0)"

# Clone public projects and run each suite before and after refactoring (141 projects;
# large, memory-bound — keep --workers low and set TOWEL_WORKERS to cap forks).
# The check executes third-party code with your privileges, so the script refuses
# unless you pass the opt-in yourself: `just ecosystem --run-untrusted-code`, on a
# disposable machine or container. The recipe forwards only what you give it.
ecosystem *ARGS:
    uv run --frozen python scripts/ecosystem_check.py --work /tmp/towel-ecosystem --workers 3 {{ARGS}}

# DANGER: overwrite the regression baselines (asks for confirmation)
regenerate-baseline:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "This OVERWRITES test_examples_expected_output/ and"
    echo "test_examples_crossfile_expected_output/. Run only after verifying new"
    echo "output is correct and all observational-equivalence tests pass."
    read -p "Type YES to continue: " response
    [ "$response" = "YES" ] || { echo "Aborted."; exit 1; }
    uv run --frozen python tests/generate_baseline.py --confirm
    echo "Baseline regenerated; commit the updated files."

# === Quality ===

# Format with black
format:
    uv run --frozen black src/towel tests scripts

# Check formatting without writing (CI form)
format-check:
    uv run --frozen black --check src/towel tests scripts

# Lint with flake8 (CI form)
lint:
    uv run --frozen flake8 src/towel scripts tests

# Type-check with mypy (CI form)
typecheck:
    uv run --frozen mypy

# Security scan with bandit (CI form)
security:
    uv run --frozen bandit -r src/towel scripts -ll

# Audit every installed third-party dependency, retaining the exact pins as evidence
audit-dependencies:
    #!/usr/bin/env bash
    set -euo pipefail
    requirements="$(mktemp "${TMPDIR:-/tmp}/towel-dependencies.XXXXXX")"
    uv run --frozen python scripts/audit_dependencies.py > "$requirements"
    echo "Auditing installed dependency pins recorded in $requirements"
    uv run --frozen python -m pip_audit --strict --no-deps --disable-pip --requirement "$requirements"

# All quality gates: formatting, lint, typing, security
check: format-check lint typecheck security
    @echo "Quality checks passed."

# Parse every mermaid diagram in the documentation with mermaid's own parser.
# Needs Node; installs mermaid and jsdom into an ignored node_modules.
# GitHub renders these and no Python check looks at them, so a syntax error
# would otherwise be found only after publication.
check-diagrams:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v npm >/dev/null; then
        echo "npm is not installed; skipping diagram check." >&2
        exit 0
    fi
    # ESM ignores NODE_PATH and resolves by walking up from the script, so the
    # packages go beside it; --no-save leaves no package.json behind.
    [ -d node_modules/mermaid ] || npm install --no-save --no-audit --no-fund --silent mermaid jsdom
    node scripts/check_mermaid.mjs docs/*.md *.md

# === Release ===

# Set the version in pyproject.toml and refresh the lockfile
bump-version VERSION:
    uv run --frozen python scripts/set_version.py {{quote(VERSION)}}
    uv lock
    uv sync --frozen --extra dev

# Build local wheel and sdist (publication and tagging are separate maintainer actions)
build:
    uv run --frozen python -m build

# Prepare a local release: bump, full checks, tests, build (does not publish or tag)
release VERSION:
    just bump-version {{quote(VERSION)}}
    just check
    just audit-dependencies
    just test
    just build
    @echo "Local distributions built. Review the audit and artifacts before publication."

# Everything CI runs: quality gates, tests, coverage gate, build
ci: check audit-dependencies coverage build
    @echo "CI checks passed."

# === Maintenance ===

# List ignored build outputs for manual review (never deletes source-shaped globs)
clean-preview:
    git clean -ndX -- build dist htmlcov .coverage

# Verify test example files match their templates
verify-examples:
    uv run --frozen python scripts/verify-examples

# Restore test example files from templates
reset-examples:
    cp .templates/*.py test_examples/
    @echo "Example files restored from templates."
