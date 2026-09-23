# Checking a project the way its own CI checks it

Status: proposal, 2026-09-23. Not scheduled. For 1.772 the owner chose a
differential baseline instead: typed mode proceeds on a project whose
baseline has errors, and rejects only a candidate that adds one (see
`docs/DECISIONS.md`). This proposal would make Towel's check agree with the
project's own. It builds on the differential baseline and does not replace
it.

## The problem

Typed mode verifies each candidate with the project's checker, against a
baseline check of the project as it was. Through 1.772's development that
baseline had to be clean, and almost no real project's was. A study of 20
projects from the release corpus, each run in its own environment with its
own typing dependencies and locked checkers, found:

- **All 17 projects that type-check passed their own check** when it was run
  as their CI runs it. The other three do not type-check at all.
- **Towel's baseline was clean for 6 of the 20.**
- **Not one error was genuine by the project's own standard.** No project's
  own check reports an error that Towel's baseline also reported.

Towel's disagreement came from checking something different, not from its
checker. Where the files, the flags and the environment were the same,
Towel's mypy verdict equalled the project's own command exactly, on seven
projects. The differences, counted per project (one project can have
several):

| Cause | Projects |
|---|---|
| Towel checks tests, benchmarks, docs or scripts outside the CI's targets | 5 |
| Missing typing dependencies or checker pins | 5 |
| A configured pyright that no CI step runs as a project check | 4 |
| No type check at all | 3 |
| The CI checks only stubs, never the implementation | 2 |
| A CI-only flag, a different checker (ty), Python-version pins | 1 each |

The corpus harness now installs projects' typing dependencies and pinned
checkers, which removes the second row. A defect in Towel's pyright session
behind two further disagreements is being fixed separately.

## What the project's own check is

Most projects state their check as a command line, not as configuration. Of
the 17, 15 are mypy or pyright invocations that can be read from:

- tox environments (7);
- pre-commit hooks (5);
- a Makefile target (1);
- a workflow step (1);
- a shell script that sets a variable (1).

The other two are cattrs' pytest plugin, which type-checks snippets
embedded in Markdown, and platformdirs' `ty`, which Towel does not run.

A command line fixes three things configuration does not:

- **The targets.** For example `mypy src/attrs`, or pre-commit's file list,
  which overrides the configuration's `files`.
- **The flags.** idna's CI passes `--strict`; anyio's hook passes
  `--ignore-missing-imports`.
- **Which checkers run at all.** click, markupsafe and jinja2 configure
  pyright, and no CI step runs it as a project check.

## The rule

When a project's check can be discovered, Towel's baseline and every
candidate check would run as that check: the same checkers, targets and
flags, in the project's environment. Two refinements keep it sound.

- **The package being refactored is always checked.** attrs' CI checks only
  its `.pyi` stubs, and cattrs' only documentation snippets. Checking just
  those would leave the code Towel changes unverified by types. Under the
  differential baseline the pre-existing errors in that code are tolerated,
  and any new one is rejected.
- **Consumers are checked only inside the CI's targets.** A test module the
  CI never type-checks cannot be broken, by the project's own standard, by a
  type change it never sees. Towel's changes preserve the names and
  signatures of everything that existed before, so a consumer outside the
  targets is one the project has chosen not to check.

Where no check can be discovered, Towel keeps the configured checkers, its
own scope and the differential baseline, and says that it did.

## Expected effect

Measured on the same 20 projects, in their own environments:

- **Exact agreement with the CI.** Taking targets and flags from the
  discovered invocation, and running only the checkers the CI runs, would
  make Towel's baseline agree exactly for 13 projects.
- **Differential for the rest.** The differential baseline would cover 6
  more: the three without a check, cattrs and platformdirs, and attrs'
  implementation.
- **One project left over.** trio's pins change with the Python version,
  and on 3.12 its checker cannot run.

It also closes two places where Towel checks *less* than the CI:

- packaging's pre-commit mypy checks `benchmarks/` and `tasks/`, which
  Towel's `files`-scoped check leaves out;
- idna's CI checks with `--strict`, and Towel does not.

## Risks and open questions

- **Discovery is heuristic.** tox factor conditions, nox sessions written in
  Python, pre-commit's file filtering, Makefile variables and CI matrices are
  each a small parser. The study read 15 of 17 correctly by hand; a tool
  would need tests for each form, and should say which source it used.
- **The CI and the local environment can differ.** A check pinned by Python
  version or platform (trio) may not run in the user's environment. The
  message should then say that the project's own check does not pass here,
  not that the project has N pre-existing errors.
- **A user may know better.** An explicit option to state the check (for
  example `--type-check "mypy src tests/typing"`) would be simpler and
  exact, and could be what the discovery falls back to or is overridden by.
- **Other checkers.** `ty` and pytest-embedded checks would stay outside the
  model until Towel can run them.

The study's scripts and per-project records are the evidence for every
figure here; they should be committed alongside an implementation.
