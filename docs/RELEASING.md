# Preparing a release

The recorded latest release is `1.618` (September 17, 2026). Version `1.732`
is in preparation and is not tagged or published. Its
[changelog](../CHANGELOG.md#1732---unreleased) summarizes reuse of existing
functions, optional formatting and typing, larger-project controls, and the
pre-release audit fixes. Runtime `1d246075` and validated source
`d4c9001` are distinguished from earlier candidates in the
[readiness report](PRODUCTION_READINESS.md).

The `347d62b` report of 118 PASS, 19 NO_CHANGE and 4 BROKEN_KNOWN among
141 projects is historical. Current coverage combines the complete r6 run
with corrected-environment runs for Cheroot, PLY, and SimPy: 119 `PASS`,
19 `NO_CHANGE`, and three `BROKEN_KNOWN`. Its explicit `--no-types` mode and
composite scope must accompany every summary; matching upstream failures are
retained in the evidence. Preparing artifacts does not authorize uploading
them, changing repository visibility, creating remote tags, or contacting users.
Publication is a separate maintainer decision.

## Recorded release history

Recorded during September 2026; verify current remote state before publication:

- [PyPI's project metadata](https://pypi.org/pypi/code-towel/json) lists versions `1.0.0` through `1.0.4`; all of their wheel and source artifacts are yanked with the reason `broken import handling`. Those versions were uploaded December 3–4, 2025. A yanked version is still used; do not rebuild and attempt to replace its files. `1.414` (September 15, 2026) and `1.618` (September 17, 2026) are published and not yanked; neither may be rebuilt or replaced. Publish the next release as a new version.
- The GitHub repository was made public on September 15, 2026, and its default branch is `main`. Earlier authenticated inspection (while private) found historical releases `v0.5.0` and `v0.5.1`, an active CI workflow, and an unprotected `main` branch. Verify the actual remote settings before relying on any of them.
- The public PyPI description still represents the earlier release. A new distribution must carry the current status documentation, known limitations, and Python requirement.

Recheck live version availability immediately before publishing. The proposed version number is not reserved.

## Local evidence required

Use Python 3.13 for the pinned quality tools and `uv sync --frozen --extra dev`.
Run `just ci`: formatting, lint, typing, Bandit, the dependency audit, full tests,
coverage with the unconditional 85% gate, and a wheel/source build. Repeat the
full tests and coverage on Python 3.11–3.13, combining coverage before each
report. If dependencies or extras changed, review and commit the refreshed
`uv.lock` before the frozen runs. Record the candidate commit, commands,
interpreter/tool versions, exit statuses, and retained evidence paths.

### Consumer and typing evidence

Run the ecosystem harness from a committed snapshot (`--towel-src` names that
snapshot's `src` directory). The harness imports it throughout the run, so do
not change that source while it executes. It runs third-party setup and tests
with your privileges; use a disposable machine or container and the explicit
`--run-untrusted-code` opt-in. Refresh upstream pins only after reviewing them.

The manifest provides runtime test environments. For the behavioral release
corpus, explicitly pass `--no-types` to the harness and record its `typing_mode`
alongside the verdict counts. A completed consumer run in that mode is not
evidence that the same projects have clean default typing baselines. Do not
reinterpret a typed refusal as NO_CHANGE or silently rerun it without types.

Verify the default policy separately: dirty original projects abort before
copying, checker failure is distinct, explicit `--no-types` preserves existing
source annotations, and clean originals retain prospective project checks,
including unchanged consumers, copied outputs, and existing-function reuse.

Only completed, nonempty test runs qualify for an accepted corpus verdict.
Preserve baseline failures and failing-test identities; investigate setup,
collection, typing-precondition, timeout, and unrecognized-run failures.
Matching isolated retests also require full-suite confirmation with the
original test count. Known failures cannot justify missing tests. Retain the
pinned manifest, requested typing mode, per-phase exit statuses, complete before/after
and retest logs, changed-file records, and the final machine-readable report.
A no-change project provides no evidence about transformed behavior.

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
