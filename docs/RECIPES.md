# Recipes

[Documentation index](README.md)

Short, tested ways to use Towel in a project's routine. The examples run in a
repository with a package under `src/shop/` and a second project under
`packages/billing/`.

## Fail a CI job when Towel would change code

`--check` makes `preview` exit with status 3 when a complete run would change
something, and 0 when it would not:

```bash
towel preview . --check --summary   # exits 3 when Towel would change code
```

```text
Would apply 3 refactoring(s).
Analysed 5 Python files (7 functions).
Would change 3 file(s):
  packages/billing/billing/totals.py: 1 refactoring(s)
    - Extract common code from total_weight and total_volume
  src/shop/notify.py: 1 refactoring(s)
    - Extract common code from notify_owner and notify_buyer
  src/shop/pricing.py: 1 refactoring(s)
    - Extract common code from checkout_total and member_total
Input files were not changed.
```

In GitHub Actions:

```yaml
- uses: astral-sh/setup-uv@v6
- run: uvx --from code-towel towel preview . --check --summary
```

`--summary` keeps the log short; drop it to see the diff in the job's output.
Status 3 is the only one that means "would change code": 1, 2, 124, 130 and 143
mean the run itself failed ([exit statuses](COMPATIBILITY.md)).

## Run Towel from pre-commit

Towel ships a pre-commit hook, `towel-check`, which runs
`towel preview . --check --summary` over the whole repository. Add it to
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/ericeallen/towel
    rev: v2.0
    hooks:
      - id: towel-check
        stages: [manual]
```

A large repository takes a while to analyse, so `stages: [manual]` runs the
hook only when you ask:

```bash
pre-commit run --hook-stage manual towel-check
```

Leave out `stages` to run it on every commit.

## Project settings

Options you would pass on every run can live in `pyproject.toml`, under
`[tool.towel]`, spelled as on the command line without the leading dashes.
Save this as `pyproject.toml`:

```toml
[project]
name = "shop"
version = "0.1.0"

[tool.black]
line-length = 88

[tool.towel]
min-lines = 4
exclude = ["packages"]
cross-module = true
```

```bash
towel preview . --summary
```

```text
Using [tool.towel] from /home/you/pyproject.toml
Would apply 2 refactoring(s).
Analysed 3 Python files (5 functions). Skipped 2 files in excluded directories.
Would change 2 file(s):
  src/shop/notify.py: 1 refactoring(s)
    - Extract common code from notify_owner and notify_buyer
  src/shop/pricing.py: 1 refactoring(s)
    - Extract common code from checkout_total and member_total
Input files were not changed.
```

Towel reads the `pyproject.toml` of the project it is given and says so on
its first line. An option on the command line wins over the file; for a list
such as `exclude`, the command line's list replaces the file's. `--no-config`
ignores the table for one run. An unknown key or a wrong value stops the run
with status 2 and lists the keys it accepts. Any option of `towel dry` or
`towel preview` can be set this way, including `strict = true`
([relaxed and strict](RELAXED_AND_STRICT.md)).

## A repository with several projects

Run Towel on each project; each uses its own `pyproject.toml`:

```bash
towel preview packages/billing --summary
```

```text
Using [tool.towel] from /home/you/packages/billing/pyproject.toml
Would apply 1 refactoring(s).
Analysed 2 Python files (2 functions).
Would change 1 file(s):
  billing/totals.py: 1 refactoring(s)
    - Extract common code from total_weight and total_volume
Input files were not changed.
```

Helpers are shared only within a module unless you pass `--cross-module`,
and then only among the modules of the target you give, so one project's
helpers never reach into another.

## A large project

Start small, and bound what a run may cost:

```bash
towel preview src/shop/pricing.py              # one module first: seconds
towel preview src/ --summary --no-types        # no type checking: several times faster
towel preview src/ --summary --time-limit 600  # give up after ten minutes, with status 124
towel dry src/ src/ --max-refactorings 2       # stop after two changes, then review them
```

`--exclude NAME` leaves out every file or directory of that name, such as
`--exclude tests` or `--exclude migrations`. Towel already skips `build/`,
`dist/`, virtual environments and files Git ignores. On a machine short of
memory, use fewer workers: `--jobs 2`, or `TOWEL_WORKERS=2` in the
environment. [Using Towel on a project](USING_TOWEL.md) shows how to review
the result, and [performance](PERFORMANCE.md) gives typical times.
