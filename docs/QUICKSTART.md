# Quick start

[Documentation index](README.md)

Towel finds repeated Python code and extracts each group of duplicates into one
helper function, rewriting the duplicates as calls. It verifies every extraction
and leaves the naming to you. Refactoring changes your code, so the workflow is:
preview, apply to a copy, review the diff, and run your tests.

## Install

```bash
pip install code-towel
towel --version
```

The PyPI package is `code-towel` and the command is `towel`. Do not install
`towel`: that name belongs to a different, unrelated project, so `pip install
towel` and `uvx towel` will not get this tool. Python 3.11–3.13 on macOS or
Linux, no runtime dependencies. Optionally, `pip install "code-towel[format,types]"`
adds the formatter and type checker Towel uses, when present, to format the
code it inserts and to annotate and type-check the helpers it generates.

## 1. See what it would change (read-only)

```bash
towel preview path/to/project
```

`preview` runs the complete `dry` pipeline in a disposable copy, including type checking, formatting, repeated extraction and final verification with the same options. It prints the final source diff and helper inventory without changing the input. Use `--quick` for a partial, untyped analysis; its proposals are not the final result or an upper bound on what `dry` can extract.

By default a helper is shared only between duplicates in one module; add `--cross-module` to share helpers between modules too, which adds imports between them.

On a large project, `--min-lines 5` or `--max-pairs N` bounds the analysis; progress is printed to stderr.

## 2. Refactor into a fresh copy

Never refactor in place on your first run — write to a new directory and diff it.

```bash
towel dry path/to/project path/to/cleaned --no-interactive
diff -ru path/to/project path/to/cleaned | less   # or redirect to a file outside a terminal
```

`--no-interactive` skips the confirmation `towel dry` would otherwise ask for.
Without it, and with a type checker installed, it first reports what
verification will involve on this project — how many files each candidate
signature is checked against and how many third-party packages their imports
pull in — and then asks before doing any of it. On a large annotated project
that is worth reading: a full run can take a while, and `--max-refactorings N`
stops after N.

If the run refuses because a file of the project does not parse on the Python
Towel runs on, run Towel on a Python that parses it, or, for deliberately
invalid test data, leave it out with the `--exclude` the refusal names.

The diff also shows one file that is not code: `.towel-helpers.json`, which
`dry` writes into the output for the naming step below. It is safe to delete.

The output compiles and, for every proposal, the generated helper is verified to
reproduce the exact code it replaced. Helpers get placeholder names like
`__extracted_func_3`. Inserted code is formatted the way the project formats
its own, and in annotated code each helper carries the annotations its call
sites declare or the project's type checker verifies, when those tools are
installed; `--no-format` and `--no-types` turn either off. A duplicate that is
the whole body of an existing function is extracted like any other: that
function and the other copies all call the new helper, and none is rewritten
to call another, which a test patching one of them would then change too.

When a type checker is available, Towel checks the complete original project
before creating output, and says what that check reports. Errors it already
reports are left as they are: a change is rejected only for an error it adds,
and a file where the checker cannot type what it imports is left alone. If the
checker cannot run at all, fix what stops it or rerun the same command with
`--no-types`, which preserves existing source annotations and leaves new
helpers unannotated. See
[Type checking and helper annotations](TYPING.md).

## 3. Give the helpers real names (optional, LLM-assisted)

Review the diff and adopt the cleaned output into its project first, using
your normal version-control workflow. The rename checker needs the package
in its real import context. The commands below assume `path/to/project` now
contains the adopted result.

```bash
towel rename-helpers path/to/project --list --json > helpers.json
# Have a coding assistant read helpers.json and write renames.json,
# then apply the batch (it aborts whole if any name is unsafe):
towel rename-helpers path/to/project --rename-file renames.json --preview
towel rename-helpers path/to/project --rename-file renames.json
```

See the [naming guide](NAMING.md) for inventory keys, class-private names
and the full review workflow.

## 4. Review and test

```bash
# run from the project containing the reviewed result:
pytest   # or your project's own test command
```

## Next

- [Command-line guide](CLI_GUIDE.md) — options, exclusions and recovery
- [Requirements](REQUIREMENTS.md) and [performance](PERFORMANCE.md) — resources and timing
- [Known limitations](KNOWN_LIMITATIONS.md) — what is verified, rejected, and outside the model
- [Python API guide](USAGE_GUIDE.md) — using `UnificationRefactorEngine` directly
- [Architecture](ARCHITECTURE.md) — how it works
