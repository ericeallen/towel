# Command reference

```bash
towel preview shop/                  # show what would change; write nothing
towel dry shop/ cleaned/             # write a changed copy; shop/ stays as it is
towel dry shop/ shop/                # change shop/ in place
towel rename-helpers cleaned/ --list # list the helpers Towel wrote, to name them
```

Towel has four commands: `preview`, `dry`, `rename-helpers` and `recover`.
`towel COMMAND --help` lists every option with its default, `towel -V` prints
the version, and `python -m towel` runs the same program. Options can also
be set for a project in `pyproject.toml` ([project settings](RECIPES.md#project-settings)).
For a first run, see the [quick start](QUICKSTART.md).

On this page:

- [preview and dry](#preview-and-dry)
- [Choose what is examined](#choose-what-is-examined)
- [Limits](#limits)
- [Output and in-place changes](#output-and-in-place-changes)
- [Recovery and interrupted writes](#recovery-and-interrupted-writes)
- [Diagnostics](#diagnostics)
- [rename-helpers and recover](#rename-helpers-and-recover)

## preview and dry

`preview TARGET` runs the whole analysis and shows the result without writing.
`dry INPUT OUTPUT` runs the same analysis and writes the result: to `OUTPUT`, a
path that does not exist yet, or to `INPUT` itself when `OUTPUT` is the same
path. Both take the same options:

| Option | Default | What it does |
| --- | --- | --- |
| `--strict` | off | Keep exactly when objects are released and names looked up, as before 2.0 ([relaxed and strict](RELAXED_AND_STRICT.md)). |
| `--cross-module` | off | Also share a helper between modules ([sharing across modules](CROSS_MODULE.md)). |
| `--types` | on | Annotate helpers in annotated code and keep only changes the project's mypy or Pyright accepts ([typed projects](TYPING.md)). |
| `--format` | on | Format inserted code with the project's Ruff or Black (configured, run by its pre-commit hooks, or already matched by every analysed file), and sort added imports as the project does. |
| `--exclude NAME` | none | Leave out every file or directory named `NAME` (repeatable). |
| `--include-ignored` | off | Also analyse files Git ignores. |
| `--complete-scope` | off | Declare that the target holds every file that uses its code, when Towel cannot find the project's root. |
| `--parameterize-builtins` | off | Extract copies that may mean different builtins, passing each caller's own ([details](GENERATED_CODE.md#names-parameters-and-other-details)). |
| `--min-lines N` | 3 | Consider only duplicates at least `N` lines long. |
| `--max-parameters N` | 5 | Extract only duplicates whose differences need at most `N` parameters. |
| `--max-refactorings N` | 0, no limit | Stop after `N` changes; 0 means no limit. |
| `--time-limit SECONDS` | none | Give up after `SECONDS`, a positive number up to 100,000,000; write nothing and exit 124. |
| `--max-pairs N` | 20,000,000 | Compare at most `N` pairs of similar blocks; 0 means no limit. |
| `--jobs N` | `TOWEL_WORKERS`, else the smallest of 8, the cores and what fits in memory | Use at most `N` worker processes; `N` must be positive. |
| `--explain` | off | List every place left alone. By default Towel shows up to five and omits declined pairs after applying changes. |
| `--stats` | off | Count what became of each pair of similar blocks. |
| `--json` | off | Print one JSON result on stdout ([automation](COMPATIBILITY.md)). |
| `--progress MODE` | `auto` | On stderr. `auto` draws a bar on a terminal and nothing in a pipe; `tqdm` uses tqdm, and warns if it is missing; `detail` logs each pass; `none` shows nothing. |
| `--quiet`, `-q` | off | Hide progress and notes; keep warnings, errors, results and the line naming a `[tool.towel]` table. |
| `--no-config` | | Ignore `[tool.towel]` in `pyproject.toml`. Command line only. |

`dry` only:

| Option | Default | What it does |
| --- | --- | --- |
| `--interactive` | on | Ask before writing. `--no-interactive` writes without asking, as scripts need. |
| `--helper-metadata` | on | Write the call sites `rename-helpers` shows: `.towel-helpers.json` in an output directory, or `OUTPUT.towel-helpers.json` beside an output file. |

`preview` only: `--summary` (counts, no diff), `--diff` (the diff alone),
`--inventory` (also list the helpers), `--quick` (a fast, partial listing of
candidates, without checks; not a prediction of the result) and `--check`
(exit 3 when the run would change code; [recipes](RECIPES.md)). `--quick` cannot
be combined with `--json`, `--summary`, `--diff`, `--inventory`, `--stats` or
`--check`; asking for one of them exits 2.

Options that switch a behavior on or off come in pairs, `--x` and `--no-x`:
`--interactive`, `--helper-metadata`, `--stats`, `--cross-module`, `--types`,
`--format`, `--strict` and `--parameterize-builtins`. Options that only ask for
something have no `--no-x` form: `--explain`, `--json`, `--check`, `--summary`,
`--diff`, `--inventory` and `--quick`. Write options in full: Towel 2.0 refuses
an abbreviation such as `--summ`.

Older scripts keep working. `--max-iterations` still means `--max-refactorings`,
and `--non-interactive` still means `--no-interactive`. `--prefer-absolute-imports`,
`--pep420` and their `--no-` forms are accepted, warn and do nothing, and
`preview --interactive` is accepted and does nothing. None of these appears in
`--help`; use the current names in new scripts.

## Choose what is examined

```bash
towel preview shop/pricing.py           # one file
towel preview shop/ --exclude tests     # leave out every file or directory named tests
towel preview shop/ --cross-module      # also share helpers between modules
```

`--exclude` takes a **name**, matched at any depth, not a path or a pattern:
to leave out `tests/**/hooks`, use `--exclude hooks`. Towel also leaves out
hidden directories, `__pycache__`, `node_modules`, `build/`, `dist/`,
`site-packages/`, `*.egg-info`, virtual environments and files Git ignores; the
summary counts what it skipped. Naming one of these directories as the target
analyzes it anyway. `--include-ignored` adds the files Git ignores, but not these
directories. If Git cannot report its ignore rules, Towel warns and includes the
files Git might ignore.

Towel reads the whole project around the target, so it knows who imports and
calls what, but changes only the target. A file that does not parse on the
Python running Towel is skipped with a warning; with `--strict` the run stops
instead and names the `--exclude` that would leave it out. A file that cannot be
decoded as text is skipped with a warning in both modes. A target that does not
end in `.py` is read as Python, with a warning.

Towel reads at most 20,000 Python files around the target. Where it needs the
whole project and the tree is larger, the run stops: give the code its own
`pyproject.toml`, `setup.cfg` or `setup.py`, or move it out of the larger tree.

### Protect a function

End a function's `def` line with the exact comment `# towel: no-extract`:

```python
def make_record(fields):  # towel: no-extract
    return build_record(fields)


def make_other_record(
    fields,
    defaults,
):  # towel: no-extract
    return build_record(fields, defaults)
```

The comment goes after the colon that ends the signature, on that line, with
nothing after it. Towel then leaves the definition, its decorators and its
body exactly as they are: it extracts nothing from it, inserts no helper into
it and never calls it in place of other code. Its line number can still
change. Mark each function whose own frame matters, including one whose
callee inspects it. To leave a whole file unchanged, use `--exclude NAME.py`.

## Limits

```bash
towel dry shop/ out/ --no-interactive --time-limit 600
towel dry shop/ shop/ --no-interactive --max-refactorings 10
```

`--time-limit` bounds setup, analysis and checking; when it expires, nothing
is written and the exit status is 124. Towel notices at its next check, so a run
can stop a little late, and writing, once started, always finishes. It is not a
memory limit. `--max-refactorings` stops after that many
changes, so you can review and commit in steps. `--max-pairs` bounds the pairs
compared, not time or memory, and 0 means no limit. For memory, use fewer
workers: `--jobs 1` or `TOWEL_WORKERS=1`.

A helper must be smaller than the copies it replaces, counting the helper and
every call, so raising `--max-parameters` or lowering `--min-lines` does not
make Towel extract everything it then could.

## Output and in-place changes

A separate output path must not already exist or overlap the input. Giving the
input path again as the output is the one exception: it refactors the project
in place. (The Python API also accepts an empty output directory.) A file input
needs a file output: naming a directory exits 1 and suggests the path to use.
When there is nothing to change, `dry` asks no question, and a separate output
still receives a copy of the input.

A run does all its work on a private copy and writes only once every check has
passed. Writing to a new output copies the whole target there. Writing in
place replaces only the changed files, each atomically, as one batch with a
journal. The batch as a whole is not atomic to other programs reading the
files: they can observe a partially applied batch. A failure Towel catches
while writing puts every file back; interrupted batches retain a recovery
journal. A failure after the files are written, such as while preparing the
naming data, leaves them written. Each file keeps its line endings, including
mixed ones, and `dry` warns about any input it could not copy.

Keep other editors off the project while Towel writes. If a file changes after
Towel read it, Towel refuses to write rather than overwrite the edit. In place,
Towel leaves alone a file with more than one hard link, and says so first.

## Recovery and interrupted writes

If an in-place run is stopped while it writes, it leaves a directory named
`.towel-transaction-<id>`, holding the original files, in the closest directory
that holds every file it was changing. That can be below the target. Give that path to `towel recover`, as in
`towel recover shop/.towel-transaction-<id>`, and it restores every file, then
removes the journal:

```text
Restored 3 files; journal removed.
```

Do not delete a journal by hand. While one is pending, a run that would
change a file it names refuses and tells you to recover first; a run that
writes a new output is not affected. If a file was edited since the
interruption, `recover` refuses and keeps both the edit and the journal, so
you can resolve the conflict. If the journal itself is damaged, the refusal
says how to restore from its numbered backups by hand.

## Diagnostics

```bash
towel preview shop/ --summary                # counts only
towel preview shop/ --diff > changes.diff    # the diff alone, for a file or a pipe
towel preview shop/ --inventory              # also list the helpers it would write
towel preview shop/ --json > result.json     # one JSON object, for scripts
towel preview shop/ --explain                # every place left alone, with its reason
towel preview shop/ --stats                  # what became of each pair compared
```

Results go to stdout; warnings, notes, questions and progress go to stderr, so
redirecting stdout keeps only the result. `--summary`, `--diff` and `--json`
exclude one another. By default a run names the five largest places it left
alone; after a run that extracted something, it omits the pairs it declined, and
`--explain` shows them. A changed file that cannot be decoded is left out of the
diff, with a warning naming it. Human-readable wording can change between releases; scripts should read `--json` and the exit
statuses in the [compatibility contract](COMPATIBILITY.md).

`--stats` counts each pair of similar blocks of the run's last analysis once:
declined, a duplicate of another pair's change, dropped for overlapping a
change kept instead, or proposed. It also counts the helper signatures the
type checker accepted and refused over the whole run. The same counts are on
`engine.run_report` in the [Python API](USAGE_GUIDE.md).

| Environment variable | Effect |
| --- | --- |
| `TOWEL_WORKERS=N` | Use at most `N` worker processes; `--jobs` overrides it. |
| `DEBUG_PROPOSAL_REJECTIONS=1` | Print a trace line for every pair declined. |
| `TOWEL_DEBUG_TYPES=1` | Print the types and errors behind each helper annotation. |
| `DEBUG_VALIDATION=1`, `DEBUG_OVERLAP_FILTER=1` | Trace pair validation and overlap filtering. |

These are read once, when Towel starts.

## rename-helpers and recover

`towel rename-helpers TARGET` renames the helpers Towel wrote and their
parameters, updating every call. `--list` shows them, `--rename-file FILE`
applies a JSON mapping, `--preview` shows the diff without writing, and
`--format` formats the files it changes, and exits 2 if the project's formatter
is not installed where Towel runs. A mapping file that is not valid JSON exits 1.
A rejected mapping changes nothing and exits 2. A mapping that is already applied
changes nothing and exits 0. `--quiet` and `--progress` work as they do for `dry`.
`--llm` is deprecated: it warns, changes nothing about the prompt, and will be
removed, so leave it out. The [naming guide](NAMING.md) covers it in full.
`towel recover JOURNAL` is described [above](#recovery-and-interrupted-writes).

[Documentation index](README.md)
