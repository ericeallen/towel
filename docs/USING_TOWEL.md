# Using Towel on a project

On a real project, let Git keep the original: work on a branch, let Towel
change the files in place, and review the change as you would a colleague's.
The examples on this page run in a Git repository holding a package, `shop/`,
and its tests, with Black installed; without Black, the first line of each output
says the code is inserted unformatted.

## Look before you change anything

```bash
git switch -c towel
towel preview shop/ --summary
```

```text
Switched to a new branch 'towel'
Note: formatting generated code with Black (project configuration).
Would apply 3 refactoring(s).
Analysed 4 Python files (8 functions).
Would change 3 file(s):
  cart.py: 1 refactoring(s)
    - Extract common code from subtotal and taxed_total
  notify.py: 1 refactoring(s)
    - Extract common code from notify_owner and notify_buyer
  pricing.py: 1 refactoring(s)
    - Extract common code from checkout_total and member_total
Input files were not changed.
```

Without `--summary`, `preview` also prints the diff. `--inventory` lists the
helpers it would write, with their parameters. On a large project, skip this
step: `dry` does the same analysis, shows the same summary, and asks before
writing anything, so a `preview` first runs the analysis twice.

## Make the change

Give `dry` the same directory twice to change it in place. It says what it
will change and asks first:

```bash
towel dry shop/ shop/
```

```text
...
3 refactoring(s) in 3 file(s): cart.py, notify.py, pricing.py; will change shop/ in place.
Write these changes? [y/N] y

Analysed 4 Python files (8 functions).
Applied 3 refactoring(s) across 3 file(s).

  cart.py: 1 refactoring(s)
    - Extract common code from subtotal and taxed_total

  notify.py: 1 refactoring(s)
    - Extract common code from notify_owner and notify_buyer

  pricing.py: 1 refactoring(s)
    - Extract common code from checkout_total and member_total

Wrote call-site before/after to shop/.towel-helpers.json (for naming; safe to delete).
```

When the directory has changes Git has not committed, the question warns you,
so that Towel's change is not mixed with yours. Commit first.

## Review it

```bash
git diff --stat
```

```text
 shop/cart.py    | 10 ++++++----
 shop/notify.py  | 13 +++++++------
 shop/pricing.py | 22 ++++++++--------------
 3 files changed, 21 insertions(+), 24 deletions(-)
```

Read each file's diff with `git diff shop/pricing.py`, as you would review a
colleague's change: is each helper something you would have written, and
does each call read well? [Reading Towel's output](GENERATED_CODE.md) explains
the forms you will meet.

## Keep some changes and drop others

Everything is in Git, so keeping part of the change is ordinary Git. To drop
one file's change:

```bash
git restore shop/notify.py
git diff --stat
```

```text
 shop/cart.py    | 10 ++++++----
 shop/pricing.py | 22 ++++++++--------------
 2 files changed, 14 insertions(+), 18 deletions(-)
```

To keep only some hunks, stage them with `git add -p`, check the result with
`git diff --cached`, then run `git restore shop/` to discard the hunks you did
not stage. Your working tree then holds exactly what you chose, so the tests
below run on it and the commit cannot bring a rejected hunk back. A helper is
only removable together with its calls, which a cross-module helper can spread
over several files, so drop a whole helper and every call or keep them all.
To keep Towel away from some code for good, mark the function with
`# towel: no-extract` or leave its file out with `--exclude`.

## Name the helpers, test, commit

`--list --json` gives the rename key of each helper and parameter, the exact
string a mapping uses:

```bash
towel rename-helpers shop/ --list --json | grep rename_key
```

```text
      "rename_key": "cart.py:Cart.__extracted_func_2",
          "rename_key": "cart.py:Cart.__extracted_func_2.self",
      "rename_key": "pricing.py:__extracted_func_0",
          "rename_key": "pricing.py:__extracted_func_0.amount",
          "rename_key": "pricing.py:__extracted_func_0._param_0",
```

Save this as `renames.json`:

```json
{
  "cart.py:Cart.__extracted_func_2": "__items_total",
  "pricing.py:__extracted_func_0": "discounted_total",
  "pricing.py:__extracted_func_0._param_0": "discount_rate"
}
```

```bash
towel rename-helpers shop/ --rename-file renames.json
python3 -m unittest
```

```text
  cart.py:Cart.__extracted_func_2 -> __items_total
  pricing.py:__extracted_func_0 -> discounted_total
  pricing.py:__extracted_func_0._param_0 -> discount_rate

Applied 9 change(s)
To restore formatting, run: black shop/cart.py shop/pricing.py
..
----------------------------------------------------------------------
Ran 2 tests in 0.000s

OK
```

Helpers are numbered across the whole run, so the method in `cart.py` is
`__extracted_func_2`. Each key is the file, relative to the target, and the
helper's name, with its class for a method; without `grep`, the inventory also
shows each helper's code and call sites ([naming guide](NAMING.md)). A
method's new name keeps its two leading underscores, which keep it private to
its class. A longer name can make a line too long, so Towel names the formatter
command to run. Passing `--format` runs it for you, and needs that formatter
installed where Towel runs (the `format` extra); without it, `--format` stops with
status 2 and renames nothing. Then commit, without the naming file Towel wrote:

```bash
rm shop/.towel-helpers.json renames.json
git commit -qam "Extract duplicated code with Towel"
git log --oneline -1 --format=%s
```

```text
Extract duplicated code with Towel
```

## Choose what Towel looks at

- **One directory or file**: give it as the target, such as `shop/` or
  `shop/pricing.py`. Towel still reads the rest of the project to see who
  calls what, but changes only the target.
- **Leave things out**: `--exclude NAME` skips every file or directory of that
  name; repeat it as needed. Towel already skips `build/`, `dist/`,
  `site-packages/`, virtual environments and files Git ignores. Naming one of
  those directories as the target analyzes it anyway.
- **Between modules**: by default a helper serves one module.
  `--cross-module` also shares one between modules, with an import
  ([sharing across modules](CROSS_MODULE.md)).
- **Larger duplicates only**: `--min-lines 6` ignores anything shorter than six lines.

To keep these choices, put them in `pyproject.toml`
([project settings](RECIPES.md#project-settings)).

## Bound a long run

A large project with type checking can take an hour. To keep a run in hand:

- `--time-limit 600` gives up after ten minutes, writes nothing and exits 124.
- `--max-refactorings 20` stops after twenty changes, so you can review a
  manageable diff, commit it and run again.
- `--no-types` skips type checking, which is most of the time. Use it on a
  project that is not type-checked anyway.
- `--jobs 2` or `TOWEL_WORKERS=2` uses fewer processes, and less memory.

If a run is stopped while it writes (Ctrl-C, a crash, a full disk), some files
may have changed and others not. The run leaves a journal named
`.towel-transaction-<id>` in the closest directory holding every file it was
changing, which can be below the target. `towel recover` with that path puts
every file back, unless you have edited one since; then it changes nothing and
names the file ([recovery](CLI_GUIDE.md#recovery-and-interrupted-writes)).

**Next:** [recipes](RECIPES.md) put Towel in CI and pre-commit.

[Documentation index](README.md)
