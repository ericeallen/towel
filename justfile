# Towel - Command Runner
# Run `just` or `just --list` to see all available commands

# Default recipe - shows help
default:
    @just --list

# Install package into the shared local environment
install:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e .

# Install development dependencies (coverage, black, flake8, mypy)
install-dev:
    uv sync --frozen --extra dev

# === User Commands ===

# Detect and fix duplicates in file or directory (interactive, writes to output)
dry INPUT OUTPUT +ARGS="":
    # Flags:
    #   --non-interactive       Skip confirmation prompt
    #   --max-iterations N      Stop after N refactorings (0 = unlimited, default 0)
    #   --progress {tqdm,auto,none}  Control progress bars (default: tqdm)
    #   --prefer-absolute-imports / --no-prefer-absolute-imports
    #   --pep420 / --no-pep420
    # Examples:
    #   just dry src/ cleaned/                           # Unlimited until fixed point
    #   just dry src/ cleaned/ --max-iterations 50       # Cap at 50
    #   just dry file.py file_out.py --non-interactive   # Non-interactive single file
    #   just dry src/ out/ --progress none               # Disable progress display
    source .venv/bin/activate && python scripts/dry {{INPUT}} {{OUTPUT}} {{ARGS}}

# Preview duplicates in file or directory (read-only)
preview TARGET:
    source .venv/bin/activate && python scripts/preview {{TARGET}}

# Promote helper stubs (defaults to tmp_out_dry_src → src)
promote-helpers ARGS="":
    source .venv/bin/activate && python scripts/promote_dry_helpers.py {{ARGS}}

# === Testing Commands ===

# Run ALL tests (unit tests + single-file + cross-file observational equivalence)
test:
    @echo "Running comprehensive test suite..."
    @echo ""
    source .venv/bin/activate && python tests/run_tests.py
    @echo ""
    @echo "Running cross-file observational equivalence tests..."
    @echo ""
    source .venv/bin/activate && python -c "from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester; from src.towel.unification.refactor_engine import UnificationRefactorEngine; engine = UnificationRefactorEngine(max_parameters=5, min_lines=4); tester = CrossFileEquivalenceTester(engine); results = tester.test_all_projects('test_examples_crossfile', verbose=True); print('\\n=== Cross-File Test Results ==='); print(f'Projects tested: {results[\"total_projects\"]}'); print(f'Total proposals: {results[\"total_proposals_tested\"]}'); print(f'Passed: {results[\"total_passed\"]}'); print(f'Failed: {results[\"total_failed\"]}'); success_rate = 100 * results['total_passed'] / results['total_proposals_tested'] if results['total_proposals_tested'] > 0 else 0; print(f'Success rate: {success_rate:.1f}%'); raise SystemExit(1 if results['total_failed'] or not results['total_proposals_tested'] else 0)"

# Run unit tests only
test-unit:
    source .venv/bin/activate && python tests/run_tests.py

# Run cross-file observational equivalence tests only
test-crossfile:
    @echo "Running cross-file observational equivalence tests..."
    @echo ""
    source .venv/bin/activate && python -c "from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester; from src.towel.unification.refactor_engine import UnificationRefactorEngine; engine = UnificationRefactorEngine(max_parameters=5, min_lines=4); tester = CrossFileEquivalenceTester(engine); results = tester.test_all_projects('test_examples_crossfile', verbose=True); print('\\n=== Cross-File Test Results ==='); print(f'Projects tested: {results[\"total_projects\"]}'); print(f'Total proposals: {results[\"total_proposals_tested\"]}'); print(f'Passed: {results[\"total_passed\"]}'); print(f'Failed: {results[\"total_failed\"]}'); success_rate = 100 * results['total_passed'] / results['total_proposals_tested'] if results['total_proposals_tested'] > 0 else 0; print(f'Success rate: {success_rate:.1f}%'); raise SystemExit(1 if results['total_failed'] or not results['total_proposals_tested'] else 0)"

# Test observational equivalence for a single file
test-file FILENAME:
    @echo "Testing observational equivalence for {{FILENAME}}..."
    source .venv/bin/activate && python -c "from tests.automatic_equivalence_tester import AutomaticEquivalenceTester; from src.towel.unification.refactor_engine import UnificationRefactorEngine; engine = UnificationRefactorEngine(max_parameters=5, min_lines=4); tester = AutomaticEquivalenceTester(engine); passed, failed, errors = tester.test_file('{{FILENAME}}'); print(f'Result: {passed}/{passed+failed} passed ({100*passed/(passed+failed) if passed+failed > 0 else 0:.1f}%)'); [print(f'  {error}') for error in errors[:5]] if errors else None; raise SystemExit(1 if failed or not passed else 0)"

# Test specific aspects
test-bindings:
    source .venv/bin/activate && python -m unittest tests.test_bindings -v

test-returns:
    source .venv/bin/activate && python -m unittest tests.test_return_values -v

test-fstrings:
    source .venv/bin/activate && python -m unittest tests.test_fstrings -v

test-orphans:
    source .venv/bin/activate && python -m unittest tests.test_orphan_detection -v

test-engine:
    source .venv/bin/activate && python -m unittest tests.test_refactoring_engine -v

test-observational:
    source .venv/bin/activate && python -m unittest tests.test_observational_equivalence -v

# Fast smoke tests exercising signature gate, pairing, unifier, extractor, and
# regression stability without running the entire 600+ test suite
test-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Running smoke test suite (fast subset)..."
    PY=".venv/bin/python"
    if [ ! -x "$PY" ]; then
        echo "Error: venv not initialized (missing .venv/bin/python). Run: just install-dev" >&2
        exit 2
    fi
    # Regression stability on expected outputs
    "$PY" -m unittest tests.test_regression.TestSingleFileRegression -q
    # Observational equivalence harness (single-file)
    "$PY" -m unittest tests.test_observational_equivalence -q
    # Engine orchestration / pairing coverage
    "$PY" -m unittest tests.test_refactor_engine_comprehensive -q
    # Core unifier correctness
    "$PY" -m unittest tests.test_unifier_core -q
    # Extractor end-to-end behaviors
    "$PY" -m unittest tests.test_extractor_comprehensive -q
    echo "\n✓ Smoke suite passed"

# Run regression tests
test-regression:
    source .venv/bin/activate && python -m unittest tests.test_regression -v

# Run public projects' own suites before and after refactoring (clones ~40 repos)
ecosystem *ARGS:
    .venv/bin/python scripts/ecosystem_check.py --work /tmp/towel-ecosystem --workers 4 {{ARGS}}

# DANGER: Regenerate regression test baseline (OVERWRITES EXPECTED OUTPUT!)
regenerate-baseline:
    #!/usr/bin/env bash
    set -euo pipefail
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║                      *** DANGER ***                          ║"
    echo "║                                                              ║"
    echo "║  THIS WILL OVERWRITE ALL BASELINE EXPECTED OUTPUT FILES!     ║"
    echo "║                                                              ║"
    echo "║  This command should ONLY be run when:                       ║"
    echo "║    1. You have made INTENTIONAL changes to the engine        ║"
    echo "║    2. You have VERIFIED the new output is CORRECT            ║"
    echo "║    3. All observational equivalence tests are PASSING        ║"
    echo "║                                                              ║"
    echo "║  The following will be COMPLETELY OVERWRITTEN:               ║"
    echo "║    - test_examples_expected_output/                          ║"
    echo "║    - test_examples_crossfile_expected_output/                ║"
    echo "║                                                              ║"
    echo "║  If you regenerate incorrectly, you will lose the ability    ║"
    echo "║  to detect regressions in refactoring output!                ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    read -p "Type YES in all caps to continue (default: NO): " response
    if [ "$response" != "YES" ]; then
        echo "Aborted. Baseline was NOT regenerated."
        exit 1
    fi
    echo ""
    echo "Regenerating baseline (using fixed-point refactoring)..."
    source .venv/bin/activate && python tests/generate_baseline.py --confirm
    echo ""
    echo "✓ Baseline regenerated successfully"
    echo "⚠️  Remember to commit the updated baseline files to git!"

# Bump project version in pyproject.toml
bump-version VERSION:
    .venv/bin/python scripts/set_version.py {{quote(VERSION)}}
    uv lock
    uv sync --frozen --extra dev

# Prepare local artifacts; publication and tagging are separate maintainer actions.
release VERSION:
    just bump-version {{quote(VERSION)}}
    just check
    just test
    .venv/bin/python -m build
    @echo "Local distributions built. Review the audit and artifacts before publication."

# Run tests with coverage report
coverage:
    @echo "Running tests with coverage analysis..."
    .venv/bin/python -m coverage run --source=src/towel tests/run_tests.py
    @echo ""
    .venv/bin/python -m coverage report

# Generate HTML coverage report
coverage-html:
    @echo "Generating HTML coverage report..."
    .venv/bin/python -m coverage run --source=src/towel tests/run_tests.py
    .venv/bin/python -m coverage html
    @echo ""
    @echo "✓ HTML coverage report generated in htmlcov/index.html"
    @echo "  Open with: open htmlcov/index.html"

# Show coverage for active unification modules only
coverage-unification:
    @echo "Running coverage for unification modules..."
    .venv/bin/python -m coverage run --source=src/towel tests/run_tests.py
    @echo ""
    .venv/bin/python -m coverage report --include="src/towel/unification/*"

# === Code Quality ===

# Format code with black
format:
    @echo "Formatting Python code with black..."
    source .venv/bin/activate && black src/towel/ tests/ scripts/ --line-length 100 --exclude="venv|env|__pycache__"

# Lint with flake8
lint:
    @echo "Linting with flake8..."
    # Pass explicit flags so config is respected even on older flake8 versions
    source .venv/bin/activate && flake8 \
        --max-line-length 100 \
        --extend-ignore E501,W503,E203,F541 \
        --exclude "venv,env,__pycache__,tests" \
        src/towel/ scripts/

# Type check with mypy
typecheck:
    @echo "Type checking with mypy..."
    source .venv/bin/activate && mypy

# Run all code quality checks
format-check:
    source .venv/bin/activate && black --check src/towel/ tests/ scripts/ --line-length 100

check: format-check lint typecheck
    @echo ""
    @echo "✓ Code quality checks completed"

# === Cleanup Commands ===

# List ignored build outputs for manual review; never delete source-shaped globs
clean-preview:
    git clean -ndX -- build dist htmlcov .coverage

# Verify test example files match their templates
verify-examples:
    python3 scripts/verify-examples

# Reset test examples to original state (restore from backup)
reset-examples:
    @echo "Restoring all example files to original state..."
    @cp .templates/*.py test_examples/
    @echo "✓ Example files restored from templates"

# === CI/CD Commands ===

# Run CI checks (what would run in continuous integration)
ci: test check
    @echo ""
    @echo "✓ CI checks passed"

# Build package
build:
    .venv/bin/python -m build

# === Help ===

# Show detailed help
help:
    @echo "Towel - Unification-Based Duplicate Code Detector"
    @echo ""
    @echo "USAGE:"
    @echo "  just <command>"
    @echo ""
    @echo "COMMON COMMANDS:"
    @echo "  dry <input> <output>  Detect and fix duplicates (writes to output)"
    @echo "  preview <target>      Preview refactoring opportunities (read-only)"
    @echo "  test                  Run ALL tests (unit + single-file + cross-file)"
    @echo "  help                  Show this help message"
    @echo ""
    @echo "TESTING:"
    @echo "  test                  Run ALL tests (unit + single-file + cross-file observational equivalence)"
    @echo "  test-unit             Run unit tests only"
    @echo "  test-regression       Run regression tests (detect output changes)"
    @echo "  test-crossfile        Run cross-file observational equivalence tests only"
    @echo "  test-file <file>      Test observational equivalence for a single file"
    @echo "  test-bindings         Test binding construct handling"
    @echo "  test-returns          Test return value propagation"
    @echo "  test-fstrings         Test f-string handling"
    @echo "  test-orphans          Test orphan variable detection"
    @echo "  test-engine           Test refactoring engine end-to-end"
    @echo "  test-observational    Test observational equivalence (refactored = original behavior)"
    @echo "  test-smoke            Run fast smoke set (regression + equivalence + engine + core unifier + extractor)"
    @echo "  regenerate-baseline   ⚠️  DANGER: Regenerate regression baseline (asks for confirmation)"
    @echo "  bump-version <ver>    Bump project version in pyproject.toml to <ver>"
    @echo "  release <ver>         Run checks, bump, and build local distributions"
    @echo "  coverage              Run tests with coverage report"
    @echo "  coverage-html         Generate HTML coverage report"
    @echo "  coverage-unification  Show coverage for unification modules"
    @echo ""
    @echo "DEVELOPMENT:"
    @echo "  format                Format code with black"
    @echo "  lint                  Lint with flake8"
    @echo "  typecheck             Type check with mypy"
    @echo "  check                 Run all code quality checks"
    @echo "  clean-preview         List ignored build outputs"
    @echo "  verify-examples       Verify test examples match templates"
    @echo "  reset-examples        Reset test examples to original state"
    @echo ""
    @echo "EXAMPLES:"
    @echo "  just dry src/ cleaned/        # Refactor src/ to cleaned/"
    @echo "  just dry my.py my_clean.py    # Refactor single file"
    @echo "  just preview src/             # Preview before refactoring"
    @echo "  just test                     # Run all tests"
    @echo ""
    @echo "For a full list of commands, run: just --list"
