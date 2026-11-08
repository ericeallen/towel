# Compatibility and automation

[Documentation index](README.md)

This contract applies to **1.949 and later releases**. The 1.949 candidate is
currently unpublished, and Towel remains beta. Correctness repairs may decline previously accepted extractions or change
the generated code; the contract does not freeze extraction counts, helper
names, formatting, timing, diagnostic wording or human-readable console output.

## Supported environments

The validated range is Python 3.11–3.13 on macOS and Linux. Package metadata
allows newer Python versions for evaluation, but the CLI warns when its
interpreter is outside that range. A successful installation on a newer
interpreter does not establish support. Windows is not supported.

Linux CI runs the full suite on each supported Python minor. The macOS Python
3.13 lane checks the installed wheel's CLI, source publication and recovery,
and checker/formatter process lifecycle. The [requirements guide](REQUIREMENTS.md)
separates these guarantees from historical resource measurements.

Optional formatters and type checkers are discovered from the environment and
project configuration. Pin their versions together with Towel when you need
repeatable output. The runtime itself has no mandatory third-party dependency.

## CLI contract

The supported commands are `preview`, `dry`, `rename-helpers` and `recover`.
Use the documented long option names in scripts. Existing supported flags will
not be removed or repurposed without a changelog migration note and a deprecation
period covering at least one published release. A repair needed to preserve
source or prevent an unsafe operation can refuse a previously accepted command
immediately; the changelog will explain the new refusal and remedy.

Always pass `--no-interactive` to unattended `dry` runs. A closed input stream
or a declined confirmation is cancellation, not completed work.

| Exit status | Meaning |
| --- | --- |
| `0` | The requested operation completed; no applicable extraction is also a completed result. |
| `1` | The operation failed, was refused, or was cancelled at confirmation. |
| `2` | Invalid command-line arguments. |
| `124` | The elapsed-time budget expired before publication. |
| `130` | Interrupted with SIGINT. |
| `143` | Terminated with SIGTERM. |

`--max-refactorings` intentionally limits the requested work and can return
success with `iteration_cap`; it does not assert that all opportunities were
exhausted. A time limit stops an incomplete attempt with status 124 and no
published output. Final publication and cleanup may extend beyond the elapsed
limit so they can finish safely. See [bounded runs](CLI_GUIDE.md#control-tools-and-work).

Human-readable output is intended for people. Errors, warnings and progress go
to stderr. Use explicit structured output for automation and check the process
exit status as well as the result. Do not scrape headings, progress bars or
generated helper names.

## Structured output

`preview --json` and `dry --json --no-interactive` write one JSON result to
stdout after argument parsing succeeds. Invalid arguments can fail before a
result is created. SIGTERM or a broken output pipe can also prevent a record
from being written. Check the exit status and require a complete result before
treating an automated run as successful.
Helper inventory and rename commands also provide their documented
`--json` forms. Use the returned inventory's `rename_key` values when preparing
a rename batch; do not reconstruct those keys from helper names.

The preview/dry result schema is version 1:

| Field | Type and meaning |
| --- | --- |
| `schema_version` | Integer `1`. |
| `command` | `"preview"` or `"dry"`. |
| `status` | `"complete"`, `"incomplete"` or `"error"`. |
| `input` | Absolute input path. |
| `output` | Absolute destination path for `dry`; `null` for preview. |
| `published` | `true` after successful `dry` publication; `false` for preview or an expired time budget; `null` when a failed or interrupted `dry` cannot confirm the publication state. |
| `applied` | Number of accepted extractions; preview counts the changes it would apply. |
| `termination` | `"fixed_point"`, `"iteration_cap"`, `"time_limit"`, `"interrupted"` or `"error"`. |
| `diff` | Unified diff for a completed preview; otherwise an empty string. |
| `helpers` | Helper inventory object, or `null` if unavailable. An optional inventory-read failure does not undo successful publication. |
| `error` | Human-readable failure explanation, or `null`. |

When `published` is `null`, inspect the destination and any recovery journal
before retrying. An explicit interrupt or error can happen after source
publication, including while preparing optional naming metadata; a nonzero
exit status alone does not establish that the destination is absent or
unchanged. Follow [recovery guidance](CLI_GUIDE.md#recovery-and-interrupted-writes).

Versioned result documents identify their schema version. Consumers must check
that version, ignore unknown object fields, and handle unknown diagnostic codes
as an ordinary failure or refusal. Additive fields may appear within a schema
version. Removing a required field, changing its type or changing its meaning
requires a new schema version and a changelog migration note.

The unversioned helper inventory and rename result formats from older releases
remain supported for their existing fields. New integrations should consume
the versioned preview/dry results and obtain naming keys from the inventory.

## Python API

Import supported library names from `towel.api`:

```python
from towel.api import ChangeConflict, UnificationRefactorEngine, apply_changes

engine = UnificationRefactorEngine(min_lines=3, max_parameters=5)
proposals = engine.analyze_file("example.py")
if proposals:
    plan = engine.plan_refactoring(proposals[0])
    for change in plan.changes:
        print(change.path, len(change.before), len(change.after))
    try:
        apply_changes(plan)
    except ChangeConflict:
        print("The project changed; analyze it again before applying.")
```

The supported surface consists of:

- `UnificationRefactorEngine` with the documented basic constructor options
  `min_lines`, `max_parameters`, `parameterize_constants`,
  `parameterize_builtins`, `excluded_directories`, `cross_module_helpers`,
  `skip_trivial_helpers`, `annotate_helpers` and `max_candidate_pairs`.
- Its `analyze_file`, `analyze_files`, `plan_refactoring`,
  `refactor_to_fixed_point` and `refactor_directory_to_fixed_point` methods,
  with their documented argument and return shapes.
- `RefactoringProposal` as an opaque, source-bound proposal returned by analysis.
  Its `description` is for display. Do not mutate proposals or depend on their
  internal AST and binding representation; reanalyze after applying a change.
- `ChangePlan`, `FileChange` and `apply_changes`. Plans expose immutable
  `changes`; each file change exposes `path`, `before`, `after` and `mode`.
- `TowelError` and `RefactoringError` for refactoring errors;
  `ChangeConflict`, `StaleSource` and `ExternalEdit` for source conflicts;
  `RecoveryRequired` when interrupted publication needs recovery. Filesystem
  operations can also raise ordinary `OSError` subclasses.

The facade re-exports the existing implementations, preserving type and exception
identity. Imports from internal modules may continue to work, but are not an
additional promise that their other names remain stable. Advanced checker,
formatter and engine-state hooks require an exact version pin and integration
tests. Supported API removals follow the same migration-note and deprecation
period as supported CLI flags.

## Upgrades and support

Versions use PEP 440 ordering, not a strict Semantic Versioning promise. Read
the [changelog](../CHANGELOG.md), upgrade in a branch, and run your project's
tests against the generated result. Pin Towel and optional tool versions in CI;
update those pins deliberately.

Security fixes are provided for the latest released version under the
[security policy](../SECURITY.md). This is a solo-maintained project with
best-effort support and no response-time commitment.
