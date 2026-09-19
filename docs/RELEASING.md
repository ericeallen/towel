# Preparing a release

The latest release is `1.618`, published to PyPI on September 17, 2026; `1.414` preceded it on September 15 (the first release after the production-readiness pass recorded in [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)). The next release is `1.732` (not yet tagged; `pyproject.toml` already carries the number). It carries everything under `[1.732]` in [CHANGELOG.md](../CHANGELOG.md): reuse of an existing function for whole-body duplicates, formatting of generated code with the project's formatter, type annotations on helpers verified by the project's type checker, the `format` and `types` extras, the paired command-line options, the candidate-pair budget and the block-size and parameter-limit options on the command line, the once-read settings, per-batch transaction journals, orderly SIGTERM and Ctrl-C handling, the rebuilt eager-argument rule and the module-name rule (a same-module helper reads module-level names bare), the restructured engine with its performance work, the exact incremental global passes, and the fixes the 141-project ecosystem run found. The ecosystem gate was rerun on the candidate (`347d62b`, September 19, 2026: 118 PASS, 19 NO_CHANGE, 4 BROKEN_KNOWN, 0 BROKEN); rerun it again if the engine changes before tagging. Preparing artifacts does not authorize uploading them, changing repository visibility, creating remote tags, or contacting users. Publication is a separate maintainer decision.

## Recorded release history

Checked on September 12, 2026:

- [PyPI's project metadata](https://pypi.org/pypi/code-towel/json) lists versions `1.0.0` through `1.0.4`; all of their wheel and source artifacts are yanked with the reason `broken import handling`. Those versions were uploaded December 3–4, 2025. A yanked version is still used; do not rebuild and attempt to replace its files. `1.414` (September 15, 2026) and `1.618` (September 17, 2026) are published and not yanked; neither may be rebuilt or replaced. Publish the next release as a new version.
- The GitHub repository was made public on September 15, 2026, and its default branch is `main`. Earlier authenticated inspection (while private) found historical releases `v0.5.0` and `v0.5.1`, an active CI workflow, and an unprotected `main` branch. Verify the actual remote settings before relying on any of them.
- The public PyPI description still represents the earlier release. A new distribution must carry the current status documentation, known limitations, and Python requirement.

Recheck live version availability immediately before publishing. The proposed version number is not reserved.

## Local evidence required

Use Python 3.13 for the pinned quality tools and `uv sync --frozen --extra dev`. Run the commands documented in the README (`just ci` runs the same set): formatting, lint, typing, Bandit, the dependency audit (`just audit-dependencies`, described below), the full tests, the unconditional 85% coverage gate (`coverage combine` before the report), and a wheel/source build. Repeat the full tests and coverage on every supported interpreter, currently Python 3.11–3.13. If dependencies or the `format`/`types`/`dev` extras changed, refresh `uv.lock` (`uv lock`) and commit it before the `--frozen` runs. Run the ecosystem check from a committed snapshot of the candidate (`--towel-src` pointing at a detached worktree), since the harness imports Towel's source live for the whole run; it executes the manifest's projects with your privileges, so run `just ecosystem --run-untrusted-code` only on a disposable machine or container, and refresh the manifest's pinned commits (`--print-pins`) only after reviewing them.

### Dependency audit

Audit the installed versions after the frozen sync. A bare `pip-audit --strict`
also tries to look up the local `code-towel` candidate on PyPI and fails before
that version is published. Export every installed third-party distribution,
excluding only `code-towel`, then audit the complete list of exact versions:

```bash
set -euo pipefail
uv sync --frozen --extra dev
requirements="$(mktemp "${TMPDIR:-/tmp}/towel-dependencies.XXXXXX")"
uv run --frozen python scripts/audit_dependencies.py > "$requirements"
uv run --frozen python -m pip_audit --strict --no-deps --disable-pip --requirement "$requirements"
```

`just audit-dependencies` runs the last three commands and prints the retained
inventory path; CI uses the same export and audit flags. The inventory includes
installed transitive dependencies, all installed extras and development tools,
and third-party editable packages. It rejects incomplete metadata and conflicting
installed versions instead of silently dropping packages. `--no-deps` and
`--disable-pip` prevent a second resolution from substituting a different set of
versions; they do not remove anything from the complete exported inventory.
`--strict` remains enabled, and no vulnerability IDs are ignored. Record the
inventory, audit date, tool version, output, and exit status. Repeat in any
supported interpreter or platform environment that resolves a different set.
The exporter itself needs only the standard library and also works in a bare
wheel environment; the audit command requires `pip-audit` in the audit environment.

### Candidate artifacts

**Before building, sweep every human-facing version reference, not just `pyproject.toml`.** `python -m build` embeds the README into the wheel and sdist as the PyPI `long_description`, and PyPI freezes that description at upload time: a published version's project page cannot be edited afterward, so a stale version string ships to PyPI and stays wrong until the next release. `just bump-version` rewrites the version in `pyproject.toml` and then refreshes `uv.lock` and the dev environment (`uv lock`, `uv sync --frozen --extra dev`); it touches no other file. After bumping, grep the tree for the outgoing version and update at least `README.md`'s `**Release status: X (beta).**` line and `SECURITY.md`'s "currently **X**" supported-version line, then rebuild so the corrected README is what gets embedded. (code-towel 1.618 shipped with the README still reading 1.414 for exactly this reason; the PyPI 1.618 page cannot be corrected.)

For the exact candidate commit:

1. Record the commit, tool/interpreter versions, commands, exit statuses, and coverage results. Review every changed golden snapshot; regeneration is not validation.
2. Run representative consumer projects' own tests before and after real transformations in disposable copies. Record upstream revisions, changed files/proposals, baseline failures or skips, and after-test results. A no-change run or compilation alone is not a consumer equivalence check.
3. Inspect both distributions, check metadata and license inclusion, install the exact candidate wheel into a clean environment, and exercise its CLI entry points on Python 3.11, 3.12, and 3.13. For each interpreter do this twice: once bare, where `towel dry` must note that no formatter or checker is installed and still refactor, and once with the `format` and `types` extras, where it must format inserted code and annotate helpers on a small annotated project. Run from outside the checkout with `PYTHONPATH` unset, and verify that the imported `towel.__file__` belongs to the new environment's `site-packages`; an import from `src/towel` tests the checkout, not the wheel. Record the exact wheel path and hash, never select from stale `dist/` files with a broad wildcard. Ensure the version matches the proposed release throughout the metadata. CI builds the distributions and runs source tests on this interpreter matrix; it does not currently perform these clean-wheel smoke checks, so retain separate evidence for them.
4. Audit the resolved runtime and development dependencies and perform a secret scan of the release contents and tracked history. Record the scope and limitations of each scan; a clean result does not guarantee absence of vulnerabilities or secrets.
5. Review the current audit report and unresolved limitations. Confirm generated documentation claims only what [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) verifies and states the remaining limitations.

`just release VERSION` prepares local artifacts. Inspect its current recipe before use; it does not replace the full evidence above or authorize publication.

## Recording the release

Once the version is chosen and the evidence is in:

1. Convert the `[Unreleased]` heading of [CHANGELOG.md](../CHANGELOG.md) to `## [X.Y] - YYYY-MM-DD` and open a new, empty `[Unreleased]` above it.
2. Add an entry at the top of [RELEASE_LOG.md](RELEASE_LOG.md): version, the commit or tag, a summary, and the test and ecosystem status.
3. Update the first paragraph of this document (the latest release, its date, and what the next one carries).
4. Tag the release commit `vX.Y` (`git tag` lists the existing ones: `v0.5.0`, `v0.5.1`, `v0.5.3`, `v0.5.4`, `v0.6.6`, `v1.414`, `v1.618`); the tag is created locally and pushed only as part of the maintainer's publication decision.

## Maintainer decisions before publication

- Choose the exact unused version and approve the reviewed artifact hashes.
- The repository is public (done September 15, 2026). Configure CI as a required check before relying on branch protection; verify the actual remote settings.
- GitHub private vulnerability reporting is enabled (done September 15, 2026), and [SECURITY.md](../SECURITY.md) documents it as the reporting channel and names the supported version (the latest release). Confirm the "Report a vulnerability" button appears on the Security tab, and revisit the support policy as the project's release cadence settles.
- Approve the publication destination and release notes. Preserve the historical yanks unless a separate reviewed decision changes their status.

No security support window or response-time promise is established by this document. Update [SECURITY.md](../SECURITY.md) when those decisions are made.
