# Preparing a release

The next version is **1.792**, named for ln 6. Its
[changelog](../CHANGELOG.md#1792) records the changes since published 1.772.
Current functionality, known limitations and the precise scope of completed
development validation are linked from the
[readiness report](PRODUCTION_READINESS.md). The versioned release needs its
own frozen source, prebuilt artifacts, completed evidence and exact-commit CI;
older development artifacts still numbered 1.772 cannot be published as 1.792.

The preceding [1.772 release](../CHANGELOG.md#1772---2026-09-26) is dated
September 26, 2026. Its corpus gave 90 `PASS`, 45 `NO_CHANGE` and six
import-problem refusals among 141 projects. Its corpus and CI observations
remain in their historical
[readiness section](PRODUCTION_READINESS.md#1772-release-validation-september-26-2026).
Preparing artifacts does not authorize uploading them, changing repository
visibility, creating remote tags, or contacting users. Publication is a
separate maintainer decision.

## Recorded release history

Recorded during September 2026; verify current remote state before publication:

- [PyPI's project metadata](https://pypi.org/pypi/code-towel/json) lists versions `1.0.0` through `1.0.4`; all of their wheel and source artifacts are yanked with the reason `broken import handling`. Those versions were uploaded December 3–4, 2025. A yanked version is still used; do not rebuild and attempt to replace its files. `1.414` (September 15, 2026), `1.618` (September 17, 2026), and `1.732` and `1.732.post1` (September 19, 2026) are published and not yanked. Version `1.772` is also published (release dated September 26; PyPI upload September 27 UTC). None may be rebuilt or replaced. Publish the next release as a new version.
- The GitHub repository was made public on September 15, 2026, and its default branch is `main`. Earlier authenticated inspection (while private) found historical releases `v0.5.0` and `v0.5.1`, an active CI workflow, and an unprotected `main` branch. Verify the actual remote settings before relying on any of them.
- PyPI embeds each uploaded distribution's README. A new distribution must carry the current version's documentation links, known limitations and Python requirement; editing GitHub afterward does not update an uploaded description.

Recheck live version availability immediately before publishing. The proposed version number is not reserved.

## Repository-owned evidence gate

The supported local release command is now:

```sh
just release VERSION /path/to/release-evidence.json
```

Choose and commit `VERSION` **before** freezing validation. This command
requires a clean checkout and verifies completed evidence for the exact
prebuilt wheel and source archive; it never changes the version or rebuilds
after verification. `just bump-version VERSION` and `just build` remain
separate development operations. The verifier neither uploads nor tags and
does not prevent an owner from invoking an upload tool manually. A successful
local check does not reserve a version or replace the final maintainer decision.

For an unreleased experiment, use
`just verify-release-evidence VERSION /path/to/release-evidence.json`.
This explicitly reports validation only, never permission to upload. The
post-1.772 frozen validation artifacts still numbered `1.772` have that scope;
they must not be relabelled as a new release. The release path also checks the
current hashed `source.json` and `artifacts.json` publication-status fields,
so changing only the bundle's purpose cannot override their no-upload status.
Historical documentation and old fixtures are not scanned for such wording.

### Evidence schema 1

Keep the bundle external to the checkout. No tracked file needs to contain
the hash of the commit that includes itself. All file references below contain
a relative path and the SHA-256 of the bytes at that path. Paths cannot escape
the bundle directory, including through symlinks. Named records may be stored
as `.gz`; their reference hashes the compressed file and the verifier reads
the decompressed record. Artifacts are always the original archive bytes.
Run the verifier from the frozen development environment (`uv sync --frozen
--extra dev`), which includes its hardened `defusedxml` parser through the
locked dependency-audit tools. Coverage evidence cannot declare a DTD or
expand custom entities, even when its file hash matches.

```json
{
  "schema": 1,
  "version": "VERSION",
  "purpose": "validation",
  "validation_commit": "FULL_FROZEN_GIT_SHA",
  "artifacts": {
    "wheel": {"path": "dist/code_towel-VERSION-py3-none-any.whl", "sha256": "SHA256"},
    "sdist": {"path": "dist/code_towel-VERSION.tar.gz", "sha256": "SHA256"}
  },
  "records": {
    "source.json": {"path": "source.json", "sha256": "SHA256"}
  },
  "review": {"path": "release-review.json", "sha256": "SHA256"},
  "amendments": null
}
```

The example abbreviates `records`: every name exported by
`scripts.verify_release_evidence.REQUIRED_RECORDS` is mandatory. List them with
`python -c 'from scripts.verify_release_evidence import REQUIRED_RECORDS; print("\n".join(REQUIRED_RECORDS))'`.
These are the retained native source/artifact inventories, five build/quality
steps, dependency/diagram checks, both 2,000-case fuzz families, all three
interpreter results with the matrix driver, full runtime coverage XML and logs, six clean-wheel command
records, sdist documentation tests, complete self-dogfood tests and integrity
records, and corpus launch/completion/environment/summary records. Also include
each `corpus-report/PROJECT.json` and every referenced raw phase log as
`corpus-logs/BASENAME`. A PASS reached through retesting also requires its
`corpus-logs/PROJECT-retest.json` and isolated/full before-and-after logs.
Missing, partial, failed or inconsistent records refuse the gate. A free-form
`status: passed` is insufficient; the verifier examines exit records, actual
pytest summaries, coverage line hits, archive contents and RECORD hashes,
corpus membership/pins, and completed before/after outcomes and failed-test IDs.

The structured review has `validation_commit`, `wheel_sha256`, `reviewer`, a
nonempty `report` file reference, `open_findings: []`, and four maps:
`reviewed_nonaccepted`, `reviewed_fallbacks`, `reviewed_known_failures`, and
`reviewed_baseline_failures`. Each map must contain exactly the projects in
that category, with no missing or stale entries. Each entry contains a
nonempty `reason`, `disposition` (`accepted-refusal`, `typed-fallback`,
`known-limitation`, or `pre-existing-baseline`) and a nonempty `evidence`
array of file references. Those dispositions respectively belong to
`reviewed_nonaccepted`, `reviewed_fallbacks`, `reviewed_known_failures` and
`reviewed_baseline_failures`.
Describe a baseline failure as pre-existing **in this run/environment**, not
as a claim about upstream CI. NO_CHANGE and early refusals do not establish
a transformed-suite result. Known differences still require pinned manifest
expectations and explicit review; a new regression is not an accepted refusal.
Every project needs a completed baseline. Only a completed CRASH or UNSUPPORTED
refactor refusal with zero changed files and no after-suite can receive
`accepted-refusal`. Setup errors, timeouts, killed processes and incomplete
suites refuse the gate regardless of review text. A known limitation must
preserve baseline failure identities and introduce only manifest-listed failures.

Native records must come from the frozen source. Where an older command
capture lacks its own source field, the bundle and review record its
provenance alongside the independently source-bound matrix/corpus/self runs.
This is a guard against incomplete or mismatched local evidence, not an
authentication mechanism: someone able to fabricate every record can also
fabricate observations. The verifier never executes retained commands.

### Changes after a validation freeze

A different HEAD is not automatically accepted. Runtime, project/build/type
configuration, dependency locks, corpus scripts/manifest, differential harness
or conftest changes require new validation. Documentation, ordinary regression
tests and retained evidence may be reconciled explicitly. Bootstrapping this
verifier permits its new script and changes confined to the justfile release
block, with the same checks; later verifier edits require a new freeze.

When these permitted files differ, `amendments` must contain `files` mapping
**every** changed repository-relative path to its current SHA-256 (or `null`
for deletion), a `report` file reference, and `checks`. Every check contains
`kind` (`tests`, `quality`, or `diagrams`), its actual `command` argument array,
timezone-aware `started`/`finished`, integer `exit_status: 0`, a `log` file
reference, the same complete `files` map, and `covers` listing the changed
files it checked. Every change requires successful supplemental tests; Python
and justfile changes also require successful `just check`. Preserve these
counts separately from the frozen matrix. Checks are tied to content, so a
subsequent edit requires recording them again.

Validation-only verification can reconcile later documentation against old
validation artifacts while retaining their exact original source identity.
For an actual release, changes to a file already shipped in those archives
refuse the gate: freeze and validate the final release contents and artifacts
instead. No version-only drift exception is provided.

## Local evidence required

Use Python 3.13 for the pinned quality tools and `uv sync --frozen --extra dev`. Run `just check-diagrams` as well: GitHub renders the documentation's mermaid diagrams and no Python check looks at them, so a syntax error would otherwise appear only once the page is published. It needs Node and installs mermaid and jsdom on first use.
Run `just ci`: formatting, lint, typing, Bandit, the dependency audit, full tests,
coverage with the unconditional 85% gate, and a wheel/source build. Repeat the
full tests and coverage on Python 3.11–3.13, combining coverage before each
report. If dependencies or extras changed, review and commit the refreshed
`uv.lock` before the frozen runs. Record the candidate commit, commands,
interpreter/tool versions, exit statuses, and retained evidence paths.

### Configured automation

This table describes the current workflow configuration, not a completed run
or a promise that the next candidate passes. Historical release results keep
their original platforms and interpreter versions in the readiness report.
CI runs for pull requests and pushes to `main` or `release/**`. Preparing a
release branch permits exact-commit push CI without changing `main`. Verify
that the completed CI run's `headSha` is the frozen release commit; a green
run on an ancestor or a different pull-request merge is insufficient.

| Job | Runner | Python | Scope |
| --- | --- | --- | --- |
| Full tests and coverage | `ubuntu-latest` | 3.11, 3.12, 3.13 | 85% coverage gate |
| Quality and distributions | `ubuntu-latest` | 3.13 | Black, Flake8, mypy, Bandit, dependency audit, build |
| Scheduled/manual corpus | `ubuntu-latest` | 3.13 | untyped (`--no-types`); 2 concurrent projects; 180-minute job limit |

`tests/test_release_documentation.py` compares this table with the workflow
files and advertised Python classifiers. It also checks the 1.772 results
against retained logs and the separately identified operator-recorded
environment in `tests/release_evidence/1.772/`. Those tests preserve timing
units, resource context, typed/untyped scope and untested platforms; they do
not run benchmarks or declare an old release validated on today's machine.

For each new release, retain completed outputs and record the source revision,
OS, interpreter/checker versions, host and container resources, concurrency,
flags and measurement units. Add its documentation checks before publication.
Correct a historical record only with corroborating evidence and a recorded
explanation. Never update an expectation solely because a new run differs.

### Consumer and typing evidence

Run the ecosystem harness from a committed snapshot (`--towel-src` names that
snapshot's `src` directory). What runs is one wheel of that source, the one
`--towel-wheel` names or one the harness builds from the snapshot when none is
named, and the harness refuses a wheel whose Python files differ from
`--towel-src`, so the commit it records is the code that ran. It runs
third-party setup and tests with your privileges; use a disposable machine or
container and the explicit `--run-untrusted-code` opt-in. Refresh upstream pins
only after reviewing them.

Towel runs in each project's own environment, as a user runs it after
`pip install "code-towel[format,types]"`. The environment holds the manifest's
test dependencies; the project itself, installed editable from the tree under
test; the tools of Towel's two extras, mypy and pyright from `types` and Black,
isort and ruff from `format`; and the candidate, installed with `--no-deps` and
then compared file by file with the wheel. A tool the project's own
requirements installed is left as it is, and one the project pins is installed
at that version. The first of three places to pin a tool decides: its
`uv.lock`, `poetry.lock` or `pdm.lock`, which is the project's environment
written down; then the `rev` of the tool's hook in `.pre-commit-config.yaml`
(`pre-commit/mirrors-mypy`, `RobertCraigie/pyright-python`,
`psf/black-pre-commit-mirror`, `PyCQA/isort`, `astral-sh/ruff-pre-commit`),
which is the version the project's own checks run, with a commit read as the
tag its `# frozen:` comment names; then an exact pin, for this interpreter, in a
requirements file, those named for typing read first (httpx's
`requirements.txt` has `mypy==1.17.1`). Only the rest come from the wheel's
extras, at the versions that resolve that day. A version
the project chose that fails the extra's requirement is replaced by the extra's,
as installing the extra replaces it, and the result names the pin it overrode:
rich's `poetry.lock` pins Black 22.12.0, below `black>=26.3.1`. Which formatter
formats a project is still Towel's choice from the project's configuration,
ruff where it configures ruff and Black otherwise; the harness only makes the
tools available.

The environment also holds what the project declares its own type check needs:
stub packages, mypy plugins, and whatever its checked code imports beyond its
runtime dependencies. The harness reads these where projects declare them:
PEP 735 dependency groups and `[project.optional-dependencies]` extras whose
names say typing, types, mypy, pyright, lint or check; the `deps`,
`dependency_groups` and `extras` of the tox environments (`tox.ini`,
`tox.toml`, `[tool.tox]`) whose commands run mypy or pyright, with their factor
conditions applied; what nox sessions that run them install; the
`additional_dependencies` of `.pre-commit-config.yaml`'s mypy and pyright
hooks; and requirements files named for typing. Each is installed at the
version the project's lock file pins where it pins one. The install only adds:
everything already in the environment is held at its version by a constraints
file, so the test dependencies, the checkers and formatters chosen above, and
the project itself cannot change, and a requirement naming one of them is left
as it is. A requirement the installer cannot add beside them is refused and
recorded, not resolved around. These dependencies expose the project's declared typing requirements, but do
not reproduce every project-owned hook or CI invocation. In particular, a
hook's Python target can differ from Towel's combined invocation; the Cheroot
gap is recorded in the [completed development validation](proposals/next-validation-final.md).
Reconcile targets, flags, dependencies and interpreter before attributing a
typed refusal to the project. Towel's import model sees the editable project
from the tree it refactors rather than an installed copy elsewhere, which it
would count as a second provider of the project's names.
The editable install follows the tree each test run exercises: the clone for
the baseline, the refactored copy for Towel and the run after it, and the
original package again for each retest of the original, so a regression in a
test that imports the installed copy cannot pass as a flaky difference. Every
project is installed except where its manifest entry says why not: wrapt,
whose compiled extension replaces the Python wrappers in every test, and
html5lib, whose `setup.py` cannot be built in isolation.

Run the corpus with the default type policy, which is what a user gets. Each
result records the interpreter, the candidate's version, each checker's and
formatter's version and who chose it (`project`; the file whose pin it is, a
lock file, `.pre-commit-config.yaml` or a requirements file by its path; or the
extra, `towel[types]` or `towel[format]`, with any pin it overrode), every typing
requirement the project declares with where it declares it and what became of
it (installed as, pinned by, or why not), the distributions the installer
added for them, the tree the project was installed from, and whether its
refactor extracted across modules; record those and the reported `typing_mode`
alongside the verdict counts. Every refactor passes `--cross-module` where the Towel under test has
that option. A Towel without it extracts across modules by default, and the
result says so. A manifest entry may turn cross-module extraction off only with
a `cross_module_reason`, and the summary lists every project that ran without
it, with its reason.

A project whose own check, as Towel runs it, already reports errors is
refactored with types all the same: each change is compared with those errors
and rejected only for an error it adds. The report records, for each such
project, how many errors there were, how many leave a name the checker cannot
type, and the files and proposals Towel declined because of them (the Typing
column and the `Typed against pre-existing errors` line). Only a project whose
checker cannot run at all is declined rather than refactored unverified, which
is the documented behaviour and not a harness failure. The answer Towel gives
such a user is to rerun without types, so that is what the corpus does, and it
holds the refusal to its promise first: the reason, and the way forward. A
refusal without the way forward is the verdict `REFUSAL_MALFORMED` and fails
the gate — the refusal is the only thing that user ever sees, and the corpus is
the only place its wording meets a real project. The report names every
project that took the untyped path and why, and those verdicts are evidence
about the untyped path only. Read the counts with that split in view: a corpus
where most projects were declined has said little about the typed one.

`REFUSAL_MALFORMED` found two projects on its first run, Lark and Voluptuous,
whose mypy configs name a Python version mypy 1.19 has dropped. The checker
could not start, and that refusal named no way out.

`--no-types` remains available and answers a narrower question: whether the
transformation preserves behaviour with verification out of the picture. It
exercises none of the type inference or checking, so a release whose changes
are in that path needs the default run as well, and a run in one mode is not
evidence about the other.

#### Running the corpus in a container

The harness clones and executes third-party code, so it belongs in a container.
Six details matter, and none of them is obvious from the failure it produces:

- **Install `uv` in the image.** The harness builds a per-project environment
  with it, and its absence surfaces as `FileNotFoundError` on every project.
- **Give the harness the candidate wheel, and install nothing of Towel's into
  the image's interpreter.** The harness installs the wheel into every
  project's environment itself (`--towel-wheel`, the wheel or a directory
  holding only it). Towel's tools in the interpreter that runs the harness are
  what used to stand in for the project's environment, hiding its dependencies
  from the type checker and putting their own copies of click, packaging and
  the rest in front of the projects of those names. Build the wheel on the
  host: building it from the mounted source fails, because the build backend
  writes `egg-info` into the source tree and that mount is read-only.
- **Put `node` on the image's `PATH`.** Without one, pyright's wrapper
  downloads a Node of about 460 MB into `HOME` on first use, and the harness
  gives every worker a scratch `HOME`.
- **Give the harness a standalone clone detached at the release commit**, not a
  git worktree. A worktree's `.git` is a file pointing outside the mount, so
  the harness cannot read the revision and aborts. With a real clone it records
  the commit, which is what makes the evidence attributable.
- **Put the work directory on a container-native volume**, never a bind mount
  from the host: `copytree` reports `ENOENT` for directories that exist when
  several workers share one. Give that volume to the container's own user, or
  the harness cannot take its lock.
- **Mount the source read-only** so the run cannot change the code it is
  testing, and pass `--towel-src` the mounted path; the harness compares the
  wheel with it before any project starts.

`scripts/ecosystem/Dockerfile` builds an image with those pieces in place, the
candidate at `/opt/towel`. A working shape, with the wheel built from the clone
under test and its copy removed once the image has it:

```sh
git clone --no-hardlinks . "$CLONE" && git -C "$CLONE" checkout --detach "$COMMIT"
uv build --wheel --out-dir "$CLONE/dist" "$CLONE"
cp "$CLONE"/dist/code_towel-*.whl "$CLONE/scripts/ecosystem/"
docker build -t "$IMAGE" "$CLONE/scripts/ecosystem"
rm "$CLONE"/scripts/ecosystem/code_towel-*.whl

docker volume create towel-eco-work
docker run --rm --user root -v towel-eco-work:/work "$IMAGE" chown -R runner:runner /work
docker run -d --name towel-eco -e TOWEL_WORKERS=1 \
    -v "$CLONE":/snapshot:ro -v towel-eco-work:/work -w /snapshot "$IMAGE" \
    python scripts/ecosystem_check.py --run-untrusted-code \
        --towel-src /snapshot/src --towel-wheel /opt/towel --work /work --workers 4
```

Verify the default policy separately: an original whose check reports errors
is refactored against them, a change that adds one is rejected and one that
adds none accepted, a file whose imports the checker cannot type is left alone,
a checker that cannot run refuses before copying, explicit `--no-types`
preserves existing source annotations, and prospective project checks cover
unchanged consumers, copied outputs, and existing-function reuse.

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

**Before building, sweep every human-facing version reference, not just `pyproject.toml`.** `python -m build` embeds the README into the wheel and sdist as the PyPI `long_description`, and PyPI freezes that description at upload time: a published version's project page cannot be edited afterward, so a stale version string ships to PyPI and stays wrong until the next release. `just bump-version` rewrites the version in `pyproject.toml` and then refreshes `uv.lock` and the dev environment (`uv lock`, `uv sync --frozen --extra dev`); it touches no other file. Before freezing, search the tree for the outgoing version and update `README.md`'s `**Release status: X (beta).**` line, its version-pinned documentation links, and `SECURITY.md`'s source-target version. Preserve historical measurements and released-version records under their original identities. The README in the built artifacts must match the reviewed source. (code-towel 1.618 shipped with the README still reading 1.414 for exactly this reason; the PyPI 1.618 page cannot be corrected.)

For the exact candidate commit:

1. Record the commit, tool/interpreter versions, commands, exit statuses, and coverage results. Review every changed golden snapshot; regeneration is not validation.
2. Run representative consumer projects' own tests before and after real transformations in disposable copies. Record upstream revisions, changed files/proposals, baseline failures or skips, and after-test results. A no-change run or compilation alone is not a consumer equivalence check.
3. Inspect both distributions, check metadata and license inclusion, install the exact candidate wheel into a clean environment, and exercise its CLI entry points on Python 3.11, 3.12, and 3.13. For each interpreter do this twice: once bare, where `towel dry` must note that no formatter or checker is installed and still refactor, and once with the `format` and `types` extras, where it must format inserted code and annotate helpers on a small annotated project, report what verification will cost before its confirmation prompt, and confirm the finished project with a cold checker. Run from outside the checkout with `PYTHONPATH` unset, and verify that the imported `towel.__file__` belongs to the new environment's `site-packages`; an import from `src/towel` tests the checkout, not the wheel. Record the exact wheel path and hash, never select from stale `dist/` files with a broad wildcard. Ensure the version matches the proposed release throughout the metadata. CI builds the distributions and runs source tests on this interpreter matrix; it does not currently perform these clean-wheel smoke checks, so retain separate evidence for them.
4. Audit the resolved runtime and development dependencies and perform a secret scan of the release contents and tracked history. Record the scope and limitations of each scan; a clean result does not guarantee absence of vulnerabilities or secrets.
5. Review the current audit report and unresolved limitations. Confirm generated documentation claims only what [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) verifies and states the remaining limitations.
6. Run the differential fuzzer on the candidate, from seeds no earlier run has covered: `just fuzz` draws a random start and prints it, and `just fuzz N SEED` repeats a run exactly. Each seed generates a small project with the round-3 semantic auditor's grammar of near-duplicate blocks. Towel refactors it in the default mode and with `--cross-module`, and every twentieth seed again typed, with the strict checker its project configures. The program is observed before and after in fresh interpreters: output, exceptions, module and class structure, and public names. The default suite runs a fixed, measured subset of these seeds (`tests/test_differential_grammar.py`), so this run is the one that covers new ground. Run the scope family too, `just fuzz N SEED --family scope`: its blocks hold their function's only binding of a name the function reads elsewhere, which the grammar never writes.
   - Each failure is written as a hostile fixture, ready to commit, under the directory the run prints, with a note saying which battery it belongs to and what to add there. Pass `--prefix` to name the fixtures by your branch. The run exits 1 if any case failed.
   - A failure of a class that [tests/audit_defects.py](../tests/audit_defects.py) names is a known defect. Commit its fixture only when it shows the class in a shape the batteries lack.
   - Any other failure is a finding for the audit, at the owner's P1 standard. Its fixture is committed as a strict expected failure, entered in its battery's `KNOWN_DEFECTS`, until the fix lands.
   - Record the seed range, the counts the run prints, and each failure. The default of 2000 seeds is about 3,700 runs; on the 1.772 candidate they took two minutes on nine worker processes (`--jobs` defaults to half the cores), and found 27 failures, every one of a class the audit had reported.

`just release VERSION` prepares local artifacts. Inspect its current recipe before use; it does not replace the full evidence above or authorize publication.

### Publication order and documentation links

PyPI renders the README embedded in the distribution metadata. Pushing a README
change to GitHub does not update an already uploaded release's description.
PyPI also does not resolve repository-relative Markdown links against GitHub.
Use absolute GitHub URLs pinned to the release tag for README links to other
repository files; fragment-only links within the README can stay relative.
Update the pinned tag in those URLs when changing the package version. The
documentation-link tests reject relative file links and mismatched tags.
`twine check --strict` checks description rendering, not whether links reach
the intended documents. Checking that PyPI received the correct README text
does not establish that its links work there; verify the URL destinations too.

Before uploading to PyPI:

1. Commit the reviewed source and documentation, build the exact distributions,
   and finish the local checks above.
2. Resolve publication permissions, including any standing prohibition on
   pushing. An agent must not upload while leaving an unresolved GitHub push
   for the maintainer afterward.
3. Push the release commit to GitHub, without its tag, and wait for all CI
   jobs on that exact commit to pass.
4. Confirm the version is still free on PyPI
   (`https://pypi.org/pypi/code-towel/json` lists every published version).
   Only then create the annotated tag on that commit, recording the artifacts'
   hashes, and push it. A tag pushed before CI passes, or for a version PyPI
   may already hold, can only be undone by deleting a public tag or by
   releasing under another number. Verify the README's documentation URLs
   against the pushed tag.
5. Upload the verified distributions to PyPI, then compare the remote hashes
   and description with the local artifacts and smoke-test a fresh installation.
6. Create the GitHub release from the existing tag with the same artifacts and
   release notes. Verify its tag, assets, and links before declaring completion.

Uploaded artifacts cannot be replaced. A documentation-only correction after
publication requires a new post-release, such as `1.732.post1`; do not delete
and try to reuse a published version.

## Recording the release

Complete these source edits before freezing and building the release:

1. Move completed changes from `[Unreleased]` into `## [X.Y]` in [CHANGELOG.md](../CHANGELOG.md) and leave an empty `[Unreleased]` above it. Add a date only if it is already known; publication metadata can record the actual date without changing frozen artifacts.
2. Add a preparation entry to [RELEASE_LOG.md](RELEASE_LOG.md) naming the version, summary, existing evidence and its limits. Record the final commit, artifact hashes, completed outcomes and CI run externally alongside the artifacts; do not edit a shipped file after its validation freeze.
3. Update the current-version summary in this document and the readiness report. Do not describe a planned check or publication as completed.
4. After exact-commit CI and the live PyPI availability check, the maintainer may tag `vX.Y` as specified above. Inspect current tags rather than relying on a historical list. Tagging and publication remain separate from preparing this source.

## Maintainer decisions before publication

- Choose the exact unused version and approve the reviewed artifact hashes.
- The repository is public (done September 15, 2026). Configure CI as a required check before relying on branch protection; verify the actual remote settings.
- GitHub private vulnerability reporting is enabled (done September 15, 2026), and [SECURITY.md](../SECURITY.md) documents it as the reporting channel and names the supported version (the latest release). Confirm the "Report a vulnerability" button appears on the Security tab, and revisit the support policy as the project's release cadence settles.
- Approve the publication destination and release notes. Preserve the historical yanks unless a separate reviewed decision changes their status.

No security support window or response-time promise is established by this document. Update [SECURITY.md](../SECURITY.md) when those decisions are made.
