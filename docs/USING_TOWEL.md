# Using Towel on a project

[Documentation index](README.md)

On a real project, let Git keep the original: work on a branch, let Towel
change the files in place, and review the change as you would a colleague's.
The examples on this page run in a Git repository holding a package, `shop/`,
and its tests.

## Look before you change anything

```bash
git switch -c towel
towel preview shop/ --summary
```

```text
Switched to a new branch 'towel'
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
helpers it would write, with their parameters.

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

`git add -p` keeps or drops single hunks. A helper is only removable together
with its calls, so drop a whole file's change rather than part of one helper.
To keep Towel away from some code for good, mark the function with
`# towel: no-extract` or leave its file out with `--exclude`.

## Name the helpers, test, commit

```bash
towel rename-helpers shop/ --list
```

```text
Found 2 extracted helper function(s):

  cart.py:13
    Function: __extracted_func_2
      def __extracted_func_2(self):
              total = 0
              for item in self.items:
      ...

  pricing.py:1
    Function: __extracted_func_0
      def __extracted_func_0(amount, _param_0):
          base = max(amount, 0)
          discount = base * _param_0
      ...
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
towel rename-helpers shop/ --rename-file renames.json --format
python3 -m unittest
```

```text
  cart.py:Cart.__extracted_func_2 -> __items_total
  pricing.py:__extracted_func_0 -> discounted_total
  pricing.py:__extracted_func_0._param_0 -> discount_rate

Applied 9 change(s)
..
----------------------------------------------------------------------
Ran 2 tests in 0.000s

OK
```

Helpers are numbered across the whole run, so the method in `cart.py` is
`__extracted_func_2`. Each key is the file, relative to the target, and the
helper's name, with its class for a method; `towel rename-helpers shop/ --list --json`
gives every key, and each helper's call sites ([naming guide](NAMING.md)). A
method's new name keeps its two leading underscores, which keep it private to
its class. `--format` formats the renamed files with the project's Ruff or
Black, since a longer name can make a line too long. Then commit, without the naming
file Towel wrote:

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
  name; repeat it as needed. Towel already skips `build/`, `dist/`, virtual
  environments and files Git ignores.
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
`.towel-transaction-<id>` in the target; `towel recover` with that path puts
every file back ([recovery](CLI_GUIDE.md#recovery-and-interrupted-writes)).
