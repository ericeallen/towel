# Quick start

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

For each opportunity, `preview` prints the extracted helper and, per call site, the original block (`-`) next to the generated call (`+`), so you can see exactly what would change before applying anything.

## 2. Refactor into a fresh copy

Never refactor in place on your first run — write to a new directory and diff it.

```bash
towel dry path/to/project path/to/cleaned --no-interactive
diff -ru path/to/project path/to/cleaned | less
```

The output compiles and, for every proposal, the generated helper is verified to
reproduce the exact code it replaced. Helpers get placeholder names like
`__extracted_func_3`. Inserted code is formatted the way the project formats
its own, and in annotated code each helper carries the annotations its call
sites declare or the project's type checker verifies, when those tools are
installed; `--no-format` and `--no-types` turn either off. A duplicate that is
the whole body of an existing function is not extracted: the other copies
call that function.

## 3. Give the helpers real names (optional, LLM-assisted)

```bash
towel rename-helpers path/to/cleaned --list --json > helpers.json
# Have a coding assistant read helpers.json and write renames.json,
# then apply the batch (it aborts whole if any name is unsafe):
towel rename-helpers path/to/cleaned --rename-file renames.json --preview
towel rename-helpers path/to/cleaned --rename-file renames.json
```

See the [README](../README.md#naming-the-helpers-with-an-llm) for the full
naming workflow.

## 4. Review and test

```bash
# adopt the cleaned copy however you version-control it, then:
pytest   # or your project's own test command
```

## Next

- [README](../README.md) — full CLI usage, requirements, and timing
- [Known limitations](KNOWN_LIMITATIONS.md) — what is verified, rejected, and outside the model
- [Python API guide](USAGE_GUIDE.md) — using `UnificationRefactorEngine` directly
- [Architecture](ARCHITECTURE.md) — how it works
