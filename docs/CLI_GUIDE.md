# Command-line guide

[Documentation index](README.md)

Use this guide for command options, input discovery, output and recovery.
For a first run, start with the [Quick start](QUICKSTART.md).

On this page:

- [Preview and refactor](#preview-and-refactor)
- [Choose the analysis scope](#choose-the-analysis-scope)
- [Control tools and work](#control-tools-and-work)
- [Output and in-place changes](#output-and-in-place-changes)
- [Recovery and interrupted writes](#recovery-and-interrupted-writes)
- [Diagnostics](#diagnostics)

## Preview and refactor

```bash
# Complete checked, formatted fixed-point preview in private temporary output.
# Shows the final diff, helper inventory and call-site before/after records.
towel preview path/to/project

# Faster partial structural listing, without checks or follow-up extractions.
towel preview path/to/project --quick

# Write into a new output directory.
towel dry path/to/project path/to/cleaned --no-interactive

# Explicit in-place refactoring; review through version control afterward.
towel dry path/to/project path/to/project --no-interactive
```

`dry` asks for confirmation unless `--no-interactive` is given. Typed `dry`
runs first describe the verification work. `preview` never prompts.

A complete preview uses the same pipeline as `dry`. The `--quick` listing is
partial: it is neither the final result nor an upper bound on what a complete
run can extract.

## Choose the analysis scope

### Share helpers within or across modules

By default, Towel extracts duplicates within one module and adds no running
import between project modules. To share helpers across modules:

```bash
towel dry path/to/project path/to/cleaned --no-interactive --cross-module
```

Cross-module extraction introduces dependencies: a helper lives in one module
and other modules import it. Towel derives valid import spellings from the
program's existing imports and checks that every borrower can import the host.

Ambiguous module names, installed copies or packaging boundaries can prevent
this. The [cross-module guide](CROSS_MODULE.md) explains the checks and the
specific remedies reported by the command.

### Exclude files and directories

```bash
towel dry path/to/project path/to/cleaned --exclude tests --exclude benchmark.py
```

Each `--exclude` is a file or directory **name**, matched at any depth. It is
repeatable and also accepted by `preview`. It is not a path or glob: to exclude
`tests/**/hooks`, use `--exclude hooks`.

Directory analysis automatically skips hidden directories, `__pycache__`,
`node_modules`, symlinked files, and virtual environments identified by
`pyvenv.cfg` or `conda-meta`. A project package called `env` or `venv` without
those markers is analyzed normally.

An exclusion leaves that source unchanged. The import model reads nothing
inside an excluded directory. Excluding a file from extraction does not
remove it from the project's configured type or build checks. Excluded files
that do not parse are treated as outside the program.

### Protect a function

Put the exact comment `# towel: no-extract` immediately after the final colon
of a function's signature:

```python
# build_record observes this factory's frame.
def make_record(fields):  # towel: no-extract
    return build_record(fields)

def make_other_record(
    fields,
    defaults,
):  # towel: no-extract
    return build_record(fields, defaults)
```

Towel leaves the protected definition and its body intact, including nested
functions and methods. It does not extract that code, rewrite it to reuse a
helper, insert a helper inside it, or reuse the protected function as a helper.
Unmarked code in the same file remains eligible for refactoring. This applies
to `def` and `async def`, to methods, to the API and CLI, and on every pass.

The marker must be a comment after the signature colon on that same line.
Put an explanation on a separate line: preceding comments, comments on a
parameter line, extra text after the directive, strings, and comments after
an inline function body are not this directive.

This protects the marked definition, not its module or every frame in its
call chain. Its absolute line position can change. Mark the functions whose
frames must remain intact, even when reflection happens in a callee. When
refactoring a directory, use `--exclude filename.py` when that file’s entire
source must remain unchanged. See the
[reflection boundary](KNOWN_LIMITATIONS.md#observable-differences-that-remain).

### Analyze a complete readable project

Towel stages the surrounding project so imports, bindings and type checks
are judged in the program's context. Even when the target is one package,
staging includes the surrounding project. This does not detect reflection or
self-instrumentation; protect sensitive definitions explicitly.

If any relevant file does not parse on the Python running Towel, every CLI
mode refuses before writing. The diagnostic names the file and parse error.
Use a Python that parses the project; for intentionally invalid test data,
use the suggested exclusion. A project requiring newer syntax may need a
newer interpreter than Towel's minimum supported version.

A project root containing more than 20,000 Python files is refused rather
than copied. Give the intended project its own `pyproject.toml`, `setup.cfg`
or `setup.py`, or move it out of the larger tree.

## Control tools and work

```bash
# Stop after at most ten applied extractions.
towel dry example.py cleaned.py --no-interactive --max-refactorings 10

# Explicitly disable typing and formatting.
towel dry path/to/project path/to/cleaned --no-interactive --no-types --no-format
```

- `--min-lines` raises the smallest candidate block size.
- `--max-parameters` limits new parameters introduced for differing expressions.
  Unchanged free inputs can also appear in an ordinary helper signature. A
  certified whole-body ownership transfer additionally requires its complete
  positional signature, including the final holder argument, to fit this limit.
- `--max-pairs` caps the candidate pairs considered in one analysis; groups
  omitted to meet the cap are reported.
- `TOWEL_WORKERS=1` keeps analysis on one worker; `TOWEL_WORKERS=N` caps it at N.
- `--parameterize-builtins` permits caller-side builtin lookup thunks across
  modules instead of the default refusal of cross-module bare builtin reads. See [generated-code rules](GENERATED_CODE.md#arguments-and-evaluation).

Formatting follows the project configuration when the relevant tools are
installed. The [generated-code guide](GENERATED_CODE.md#comments-and-formatting)
explains how comments, directives and import order are preserved.
The [typing guide](TYPING.md) explains checker selection and typed declines.

Progress is written to stderr so stdout can be redirected. Optional `tqdm`
adds progress bars that name the proposal being evaluated and refresh on a
heartbeat. There is no elapsed-time budget; see [performance and timing](PERFORMANCE.md)
for workload limits and measurements.

Run `towel dry --help` for all current options and defaults. Boolean options
have `--x` and `--no-x` forms.

## Output and in-place changes

A separate CLI output path must not already exist or overlap the input.
The Python API also accepts an empty output directory for integration and
fixture workflows.

Every run refactors a private stage and completes its final type-check
confirmation before publishing output. An out-of-place run copies the target
in full and writes the refactored target to the output path. An in-place run
writes only changed files, as one journaled batch.

Each file replacement is atomic, but the whole batch is not atomic to readers:
they can observe a partially applied batch. Caught application failures roll
back; interrupted batches retain a recovery journal.

Keep exclusive write access to the project and its parent while applying or
recovering. Snapshot checks detect stale source, but cannot prevent another
editor from writing in the final check/replace interval. A detected edit
refuses the write rather than overwriting it.

In-place runs leave hard-linked files unchanged and name them before starting:
replacing one would leave its other links with the old text. Other eligible
files can still be written. Symlinked Python files are excluded from directory
analysis.

## Recovery and interrupted writes

Recovery journals are `.towel-transaction-<id>` directories at a batch's common
root. They retain original source bytes until the interrupted operation is
resolved. Review a journal before recovery and do not delete it first.

```bash
towel recover /path/to/.towel-transaction-<id>
```

A path through a directory symlink, such as macOS's `/tmp`, can name the same
journal. The journal itself must not be a symlink, must belong to the current
user, and must be owner-only (mode 0700). Recovery reports which condition
failed if it refuses.

### When a journal blocks a run

A pending journal blocks a later write only if its manifest names a file that
write would change. An unreadable manifest conservatively covers every file
beneath the journal's root.

An out-of-place run changes none of those files, so it is not blocked. It
warns that its inputs may include files left by an interrupted change, names
the journal and remedy, and copies no journal into its stage or output.

An in-place run checks journals before starting. Its refusal names the
recovery command, or explains why automatic recovery cannot proceed.

### Conflicting edits or damaged journals

If a file has been edited since the interrupted write, resolve that conflict
before recovering. Recovery refuses detected conflicts and preserves the
journal.

If the manifest or backup is damaged, restore manually from the journal's
numbered backups. Then use the exact `mv` command in the diagnostic to set
that journal aside as `.towel-set-aside-<id>`, which later runs do not read.
The refusal explains this procedure rather than silently discarding backups.

## Diagnostics

A `dry` run reports declined proposals by reason, such as a type-check refusal,
a caller-narrowing boundary, class-attribute declarations, or text that cannot
be represented in the file's encoding. If nothing was applied, it also
summarizes candidate-pair declines.

| Environment setting | What it shows |
| --- | --- |
| `DEBUG_PROPOSAL_REJECTIONS=1` | Reasons for individual candidate-pair declines. |
| `TOWEL_DEBUG_TYPES=1` | Revealed expression types and errors behind annotation fallback. |
| `DEBUG_VALIDATION=1` | Pair-validation stages. |
| `DEBUG_OVERLAP_FILTER=1` | Overlap filtering. |

These settings, like `TOWEL_WORKERS`, are read once at startup.
For reported limits, see [Known limitations](KNOWN_LIMITATIONS.md).
