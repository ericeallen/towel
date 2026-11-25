# Compatibility and automation

A script runs Towel with `--json`, then reads the exit status and one JSON object:

```text
$ towel preview src/ --check --json > result.json
$ echo $?
3
$ python3 -c "import json; r = json.load(open('result.json')); print(r['status'], r['outcome'], r['applied'])"
complete applied 1
```

Status 3 means the run finished and would change code. The rest of this page
says what each status and field promises.

This contract has applied since **1.949**; what 2.0 added is marked "Added
in 2.0". Towel remains beta.
Correctness repairs may decline previously accepted extractions or change
the generated code; the contract does not freeze extraction counts, helper
names, formatting, timing, diagnostic wording or human-readable console output.

## Supported environments

The validated range is Python 3.11–3.15 on macOS and Linux. Package metadata
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
Use the documented long option names in scripts, spelled in full: since 2.0
an abbreviation such as `--summ` is refused with status 2. Existing supported flags will
not be removed or repurposed without a changelog migration note and a deprecation
period covering at least one published release. A repair needed to preserve
source or prevent an unsafe operation can refuse a previously accepted command
immediately; the changelog will explain the new refusal and remedy.
`rename-helpers --llm` is deprecated as of 2.0: it still works, prints a
warning, and will be removed in a later release.

A `[tool.towel]` table in the project's `pyproject.toml` sets defaults for
`preview` and `dry` (added in 2.0). Its keys are those commands' long option
names without the dashes (`strict = true`, `jobs = 4`), so the option-name
promise above covers them too; an option given on the command line overrides
the table, and `--no-config` ignores it. A table can set any of these options,
including `check` and `json`, so the exit status and output form of a run
depend on it: pass `--no-config` in automation that must not inherit a
project's settings.

Always pass `--no-interactive` to unattended `dry` runs. A closed input stream
or a declined confirmation is cancellation, not completed work.

| Exit status | Meaning |
| --- | --- |
| `0` | The requested operation completed; no applicable extraction is also a completed result. |
| `1` | The operation failed, was refused, or was cancelled at confirmation. |
| `2` | Invalid command-line arguments. Also an invalid or unreadable `[tool.towel]` table (added in 2.0). For `rename-helpers`, also a rename mapping that was rejected or a consumer scope that could not be established; nothing is renamed (added in 2.0). |
| `3` | `preview --check` completed and would change code. Added in 2.0. |
| `124` | The elapsed-time budget expired before publication. |
| `130` | Interrupted with SIGINT. |
| `143` | Terminated with SIGTERM. |

`--max-refactorings` intentionally limits the requested work and can return
success with `iteration_cap`; it does not assert that all opportunities were
exhausted. A time limit stops an incomplete attempt with status 124 and no
published output. A limit that expires during setup leaves `analysed` `null` and
no `partial_diff`; one that expires later can return a partial diff, which Towel
has not checked as a whole. Final publication and cleanup may extend beyond the elapsed
limit so they can finish safely. See [bounded runs](CLI_GUIDE.md#limits).

Human-readable output is intended for people. Errors, warnings and progress go
to stderr. Use explicit structured output for automation and check the process
exit status as well as the result. Do not scrape headings, progress bars or
generated helper names.

## Structured output

`preview --json` and `dry --json --no-interactive` write one JSON result to
stdout after argument parsing succeeds. Invalid arguments, or an invalid
`[tool.towel]` table, can fail before a result is created. SIGTERM or a broken output pipe can also prevent a record
from being written. Check the exit status and require a complete result before
treating an automated run as successful.
Helper inventory and rename commands also provide their documented
`--json` forms. Use the returned inventory's `rename_key` values when preparing
a rename batch; do not reconstruct those keys from helper names. With
`rename-helpers --rename-file FILE --json`, a well-formed mapping that is rejected
prints a JSON result and exits 2. A file that cannot be read or is not valid JSON
prints an error on stderr, nothing on stdout, and exits 1.

The preview/dry result schema is version 1:

| Field | Type and meaning |
| --- | --- |
| `schema_version` | Integer `1`. |
| `command` | `"preview"` or `"dry"`. |
| `mode` | `"relaxed"`, the default, or `"strict"` when the run was given `--strict`. Added in 2.0; a result without it came from an earlier release, which had only strict behavior. |
| `status` | `"complete"`, `"incomplete"` or `"error"`. |
| `types` | `"verified"` means a checker ran and verified the accepted changes, `"unverified"` means none did (no checker was available, or, in relaxed mode, the configured checker was missing or the project's own check could not run), and `"off"` means `--no-types`; absent when an available checker had no readable source to check, or on failed or interrupted runs (see `status` and `error`). On a preview stopped by its time limit it describes the changes accepted before the stop, each checked on its own; the partial diff was not checked as a whole. Added in 2.0. |
| `input` | Absolute input path. |
| `output` | Absolute destination path for `dry`; `null` for preview. |
| `published` | `true` after successful `dry` publication; `false` for preview or an expired time budget; `null` when a failed or interrupted `dry` cannot confirm the publication state. |
| `applied` | Number of accepted extractions; preview counts the changes it would apply. |
| `applied_by_file` | Number of accepted refactorings touching each changed file, keyed by relative path. One cross-module refactoring counts once in `applied` and once under each file it changes. Empty on failure. Added in 2.0.1. |
| `termination` | `"fixed_point"`, `"iteration_cap"`, `"time_limit"`, `"interrupted"` or `"error"`. |
| `diff` | Unified diff for a completed preview; otherwise an empty string. |
| `helpers` | Helper inventory object, or `null` if unavailable. An optional inventory-read failure does not undo successful publication. |
| `error` | Human-readable failure explanation, or `null`. |
| `config_path` | Absolute path of the `pyproject.toml` whose `[tool.towel]` table the run used, or `null`. Added in 2.0. |
| `outcome` | On a completed run, `"applied"`, `"all_left_alone"` (similar code found, none extracted), `"no_repeated_code"`, `"not_fully_examined"` (no repeated code in what was examined, but some code was not examined; see `analysed.not_examined`) or `"no_python_files"`; `null` on an incomplete or failed run. Added in 2.0. |
| `analysed` | `{"files": N, "functions": M, "skipped": [...], "not_examined": [...]}`: what the run read, each file it skipped with the reason, and each file with code it did not examine (`path`, `module_statements`, `early_return_guards`, and optionally `limited_functions`). This census is `null` when the run failed (`status` `"error"`) or a time limit expired before Towel had read the project. Each entry in `limited_functions` gives the input function name (`function`), its definition line (`line`), the number of eligible statement blocks (`windows`) and the number retained for comparison within that function (`limit`), after pair-budget exclusions. If several passes limit the same function, these counts come from the pass that omitted the most blocks. Added in 2.0. |
| `left_alone` | How many places with similar code the run lists as left alone by default, counting all of them although a summary prints the five largest. A run that applied changes does not list the pairs it declined, so this can be `0` where `--explain` shows more. `0` on failure. Added in 2.0. |
| `relaxed_findings` | In relaxed mode, code that may depend on what strict mode keeps: a list of `{"kind", "path", "line", "detail"}`, where `kind` is `"finalizer"`, `"weakref_callback"`, `"gc_callback"` or `"function_rebinding"`; an empty list in strict mode. Added in 2.0. |
| `partial_diff`, `finally_confirmed` | Only on a preview stopped after it had done some work (`status` `"incomplete"`): the diff of what it had done, which was not checked as a whole, and `false`. Absent when the run stopped during setup. Added in 2.0. |

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
  `skip_trivial_helpers`, `annotate_helpers`, `max_candidate_pairs` and
  `strict` (added in 2.0). `strict` defaults to `False`, relaxed mode, as the
  CLI does; earlier releases behaved as `strict=True` does now, so pass it to
  keep their behavior. Only `max_parameters`, `min_lines` and
  `parameterize_constants` may be passed by position, in that order; the others
  are keyword-only.
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
the [changelog](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md), upgrade in a branch, and run your project's
tests against the generated result. Pin Towel and optional tool versions in CI;
update those pins deliberately.

Security fixes are provided for the latest released version under the
[security policy](https://github.com/ericeallen/towel/blob/main/SECURITY.md). This is a solo-maintained project with
best-effort support and no response-time commitment.

[Documentation index](README.md)
