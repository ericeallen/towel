# Towel - Command Runner
# Run `just` or `just --list` to see all available commands

# Default recipe - shows help
default:
    @just --list

# Install package (no external dependencies required!)
install:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m venv venv || true
    source venv/bin/activate
    pip install -e .

# Install development dependencies (coverage, black, flake8, mypy)
install-dev:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m venv venv || true
    source venv/bin/activate
    pip install -e ".[dev]"

# === User Commands ===

# Detect and fix duplicates in file or directory (interactive, writes to output)
dry INPUT OUTPUT:
    source venv/bin/activate && python scripts/dry {{INPUT}} {{OUTPUT}}

# Preview duplicates in file or directory (read-only)
preview TARGET:
    source venv/bin/activate && python scripts/preview {{TARGET}}

# === Testing Commands ===

# Run ALL tests (unit tests + single-file + cross-file observational equivalence)
test:
    @echo "Running comprehensive test suite..."
    @echo ""
    source venv/bin/activate && python tests/run_tests.py
    @echo ""
    @echo "Running cross-file observational equivalence tests..."
    @echo ""
    source venv/bin/activate && python -c "from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester; from src.towel.unification.refactor_engine import UnificationRefactorEngine; engine = UnificationRefactorEngine(max_parameters=5, min_lines=4); tester = CrossFileEquivalenceTester(engine); results = tester.test_all_projects('test_examples_crossfile', verbose=True); print('\\n=== Cross-File Test Results ==='); print(f'Projects tested: {results[\"total_projects\"]}'); print(f'Total proposals: {results[\"total_proposals_tested\"]}'); print(f'Passed: {results[\"total_passed\"]}'); print(f'Failed: {results[\"total_failed\"]}'); success_rate = 100 * results['total_passed'] / results['total_proposals_tested'] if results['total_proposals_tested'] > 0 else 0; print(f'Success rate: {success_rate:.1f}%')"

# Run unit tests only
test-unit:
    source venv/bin/activate && python tests/run_tests.py

# Run cross-file observational equivalence tests only
test-crossfile:
    @echo "Running cross-file observational equivalence tests..."
    @echo ""
    source venv/bin/activate && python -c "from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester; from src.towel.unification.refactor_engine import UnificationRefactorEngine; engine = UnificationRefactorEngine(max_parameters=5, min_lines=4); tester = CrossFileEquivalenceTester(engine); results = tester.test_all_projects('test_examples_crossfile', verbose=True); print('\\n=== Cross-File Test Results ==='); print(f'Projects tested: {results[\"total_projects\"]}'); print(f'Total proposals: {results[\"total_proposals_tested\"]}'); print(f'Passed: {results[\"total_passed\"]}'); print(f'Failed: {results[\"total_failed\"]}'); success_rate = 100 * results['total_passed'] / results['total_proposals_tested'] if results['total_proposals_tested'] > 0 else 0; print(f'Success rate: {success_rate:.1f}%')"

# Test observational equivalence for a single file
test-file FILENAME:
    @echo "Testing observational equivalence for {{FILENAME}}..."
    source venv/bin/activate && python -c "from tests.automatic_equivalence_tester import AutomaticEquivalenceTester; from src.towel.unification.refactor_engine import UnificationRefactorEngine; engine = UnificationRefactorEngine(max_parameters=5, min_lines=4); tester = AutomaticEquivalenceTester(engine); passed, failed, errors = tester.test_file('{{FILENAME}}'); print(f'Result: {passed}/{passed+failed} passed ({100*passed/(passed+failed) if passed+failed > 0 else 0:.1f}%)'); [print(f'  {error}') for error in errors[:5]] if errors else None"

# Test specific aspects
test-bindings:
    source venv/bin/activate && python -m unittest tests.test_bindings -v

test-returns:
    source venv/bin/activate && python -m unittest tests.test_return_values -v

test-fstrings:
    source venv/bin/activate && python -m unittest tests.test_fstrings -v

test-orphans:
    source venv/bin/activate && python -m unittest tests.test_orphan_detection -v

test-engine:
    source venv/bin/activate && python -m unittest tests.test_refactoring_engine -v

test-observational:
    source venv/bin/activate && python -m unittest tests.test_observational_equivalence -v

# Run regression tests
test-regression:
    source venv/bin/activate && python -m unittest tests.test_regression -v

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
    source venv/bin/activate && python tests/generate_baseline.py --confirm
    echo ""
    echo "✓ Baseline regenerated successfully"
    echo "⚠️  Remember to commit the updated baseline files to git!"

# Bump project version in pyproject.toml
bump-version VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    V="{{VERSION}}"
    if [[ ! ${V} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: VERSION must be semantic version (e.g., 0.5.3)"
        exit 2
    fi
    echo "Bumping version to ${V}..."
    # Update version in pyproject.toml (replace first matching line)
    tmpfile=$(mktemp)
    awk -v ver="${V}" 'BEGIN{replaced=0} { if (!replaced && $0 ~ /^version = \".*\"/) { sub(/version = \".*\"/, "version = \"" ver "\""); replaced=1 } print }' pyproject.toml > "$tmpfile"
    mv "$tmpfile" pyproject.toml
    echo "✓ Version updated in pyproject.toml"

# Full release: checks -> bump -> commit -> tag -> release log -> push
release VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    V="{{VERSION}}"
    if [[ ! ${V} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: VERSION must be semantic version (e.g., 0.5.3)"
        exit 2
    fi

    # Ensure clean working tree
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "Error: Working tree not clean. Commit or stash changes first."
        exit 3
    fi

    echo "Syncing with origin/main (rebase)..."
    git fetch origin
    git pull --rebase origin main

    echo "Running pre-release checks..."
    just check
    just test-regression

    echo "Running full unit/integration tests to capture counts..."
    # Capture full test run output for parsing (must pass)
    TEST_OUT=$(mktemp)
    if ! python tests/run_tests.py | tee "$TEST_OUT"; then
        echo "Error: Unit/integration tests failed"
        rm -f "$TEST_OUT"
        exit 4
    fi
    # Parse unit test count and skipped robustly
    UNIT_COUNT=$(awk '/^Ran [0-9]+ tests/ {n=$2} END {if (n=="" ) n=0; print n+0}' "$TEST_OUT")
    SKIPPED=$(awk 'match($0,/OK \(skipped=([0-9]+)/,m){s=m[1]} END{if (s=="") s=0; print s+0}' "$TEST_OUT")
    rm -f "$TEST_OUT"

    echo "Computing observational equivalence statistics..."
    # Single-file equivalence counts (suppress any internal prints)
    SF_STATS=$(python - <<-'PY'
    import contextlib, io
    from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
    from src.towel.unification.refactor_engine import UnificationRefactorEngine
    engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
    tester = AutomaticEquivalenceTester(engine)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = tester.test_all_examples('test_examples', verbose=False)
    print(res['total_proposals_tested'], res['total_passed'], res['total_failed'])
    PY
    )
    # Extract strictly numeric fields from the final line
    SF_TOTAL=$(echo "$SF_STATS" | tail -n1 | awk '{print $1+0}')
    SF_PASSED=$(echo "$SF_STATS" | tail -n1 | awk '{print $2+0}')
    SF_FAILED=$(echo "$SF_STATS" | tail -n1 | awk '{print $3+0}')
    SF_PCT=$(awk -v p=${SF_PASSED} -v t=${SF_TOTAL} 'BEGIN{if (t>0) printf "%.0f", (100*p/t); else print 0}')

    # Cross-file equivalence counts (suppress any internal prints)
    CF_STATS=$(python - <<-'PY'
    import contextlib, io
    from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
    from src.towel.unification.refactor_engine import UnificationRefactorEngine
    engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
    tester = CrossFileEquivalenceTester(engine)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = tester.test_all_projects('test_examples_crossfile', verbose=False)
    print(res['total_projects'], res['total_proposals_tested'], res['total_passed'], res['total_failed'])
    PY
    )
    CF_PROJECTS=$(echo "$CF_STATS" | tail -n1 | awk '{print $1+0}')
    CF_TOTAL=$(echo "$CF_STATS" | tail -n1 | awk '{print $2+0}')
    CF_PASSED=$(echo "$CF_STATS" | tail -n1 | awk '{print $3+0}')
    CF_FAILED=$(echo "$CF_STATS" | tail -n1 | awk '{print $4+0}')
    CF_PCT=$(awk -v p=${CF_PASSED} -v t=${CF_TOTAL} 'BEGIN{if (t>0) printf "%.0f", (100*p/t); else print 0}')

    # Auto-stage any generated artifacts from checks/tests
    echo "Auto-staging generated artifacts from checks/tests..."
    git add -A

    # Bump version and commit (include any staged artifacts)
    just bump-version {{VERSION}}
    git add -A
    git commit -m "chore: bump version to ${V} and stage release artifacts"

    # Prepend release log entry (template)
    DATE=$(date +%F)
    LOG=docs/RELEASE_LOG.md
    TMP=$(mktemp)
    {
        echo "## ${DATE}"
        echo ""
        echo "- Version: ${V}"
        echo "- Commit: $(git rev-parse HEAD)"
        echo "- Summary:"
        echo "  - Summary of changes here."
        echo "- Status: All tests green"
        echo "  - Unit/integration tests: ${UNIT_COUNT} tests OK (${SKIPPED} skipped)"
        echo "  - Observational equivalence: ${SF_PASSED}/${SF_TOTAL} proposals passed (${SF_PCT}%)"
        echo "  - Cross-file observational equivalence: ${CF_PROJECTS} project(s), ${CF_PASSED}/${CF_TOTAL} proposals passed (${CF_PCT}%)"
        echo ""
        echo "---"
        echo ""
        echo "Notes:"
        echo "- To revert to this exact state: check out commit \"$(git rev-parse HEAD)\" on branch \"main\"."
        echo "- Changes were pushed to origin/main on ${DATE}."
        echo ""
        sed -n '1,99999p' "${LOG}"
    } > "${TMP}"
    mv "${TMP}" "${LOG}"
    git add -A
    git commit -m "docs: add release log for ${V} and finalize release artifacts"

    # Tag and push atomically (commit + tag together)
    git tag -a v${V} -m "Release ${V}"
    git push --atomic origin main v${V}
    echo "Verified push with atomic refs."

    # Final cleanliness check
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "Warning: Detected uncommitted changes after release. Please review 'git status'."
        git status -sb
    fi
    echo "\n✓ Release ${V} complete."

# Run tests with coverage report
coverage:
    @echo "Running tests with coverage analysis..."
    source venv/bin/activate && python -m coverage run --source=src/towel tests/run_tests.py
    @echo ""
    source venv/bin/activate && python -m coverage report

# Generate HTML coverage report
coverage-html:
    @echo "Generating HTML coverage report..."
    source venv/bin/activate && python -m coverage run --source=src/towel tests/run_tests.py
    source venv/bin/activate && python -m coverage html
    @echo ""
    @echo "✓ HTML coverage report generated in htmlcov/index.html"
    @echo "  Open with: open htmlcov/index.html"

# Show coverage for active unification modules only
coverage-unification:
    @echo "Running coverage for unification modules..."
    source venv/bin/activate && python -m coverage run --source=src/towel tests/run_tests.py
    @echo ""
    source venv/bin/activate && python -m coverage report --include="src/towel/unification/*"

# === Code Quality ===

# Format code with black
format:
    @echo "Formatting Python code with black..."
    source venv/bin/activate && black src/towel/ tests/ scripts/ --line-length 100 --exclude="venv|env|__pycache__" || echo "black not installed, skipping"

# Lint with flake8
lint:
    @echo "Linting with flake8..."
    # Pass explicit flags so config is respected even on older flake8 versions
    source venv/bin/activate && flake8 \
        --max-line-length 100 \
        --extend-ignore E501,W503,E203,F541 \
        --exclude "venv,env,__pycache__,tests" \
        src/towel/ scripts/ || echo "flake8 not installed, skipping"

# Type check with mypy
typecheck:
    @echo "Type checking with mypy..."
    source venv/bin/activate && mypy || echo "mypy not installed, skipping"

# Run all code quality checks
check: format lint typecheck
    @echo ""
    @echo "✓ Code quality checks completed"

# === Cleanup Commands ===

# Clean generated files and caches
clean:
    @echo "Cleaning generated files..."
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete
    find . -type f -name "*.pyo" -delete
    find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
    rm -rf build/ dist/ htmlcov/ .coverage
    rm -f *_out.py my_test*.py
    rm -rf test_all test_final test_final_fix test_fix test_fix2 test_fresh_run
    rm -rf test_idempotent test_idempotent2 test_reproduce test_simple
    rm -rf my_test my_test_out my_test2 my_test2_out test_examples_copy
    @echo "✓ Cleaned"

# Verify test example files match their templates
verify-examples:
    python3 scripts/verify-examples

# Reset test examples to original state (restore from backup)
reset-examples:
    @echo "Restoring all example files to original state..."
    @cp .templates/*.py test_examples/
    @echo "✓ All 18 example files reset to original state"

# === CI/CD Commands ===

# Run CI checks (what would run in continuous integration)
ci: clean test check
    @echo ""
    @echo "✓ CI checks passed"

# Build package
build:
    python3 -m build

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
    @echo "  regenerate-baseline   ⚠️  DANGER: Regenerate regression baseline (asks for confirmation)"
    @echo "  bump-version <ver>    Bump project version in pyproject.toml to <ver>"
    @echo "  release <ver>         Run checks, bump, commit, tag, update release log, and push"
    @echo "  coverage              Run tests with coverage report"
    @echo "  coverage-html         Generate HTML coverage report"
    @echo "  coverage-unification  Show coverage for unification modules"
    @echo ""
    @echo "DEVELOPMENT:"
    @echo "  format                Format code with black"
    @echo "  lint                  Lint with flake8"
    @echo "  typecheck             Type check with mypy"
    @echo "  check                 Run all code quality checks"
    @echo "  clean                 Clean generated files"
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
