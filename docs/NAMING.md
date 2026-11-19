# Naming helpers

Towel can tell that code repeats, not what it means, so it names helpers
`__extracted_func_0` and the values that differ `_param_0`. `towel rename-helpers`
gives them real names, and updates every call, in one checked step.

This page needs a helper Towel wrote that you have not named yet. If you have
followed the [quick start](QUICKSTART.md) to the end, its helper already has
a name, so make a new project here, with the quick start's code as it was
before Towel changed it:

```bash
mkdir shop
printf '[project]\nname = "shop"\nversion = "0.1.0"\n' > shop/pyproject.toml
```

Save this as `shop/charges.py`:

```python
def checkout_total(amount):
    base = max(amount, 0)
    discount = base * 0.10
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    return round(discounted + shipping + tax, 2)


def member_total(amount):
    base = max(amount, 0)
    discount = base * 0.25
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    return round(discounted + shipping + tax, 2)


def wholesale_total(amount):
    base = max(amount, 0)
    discount = base * 0.40
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    return round(discounted + shipping + tax, 2)
```

Then let Towel write a refactored copy:

```bash
towel dry shop named --no-interactive
```

`--list --json` gives the full inventory:

```bash
towel rename-helpers named --list --json
```

```json
{
  "target": "named",
...
  "helpers": [
    {
      "file": "charges.py",
      "line": 1,
      "name": "__extracted_func_0",
      "scope": "module",
      "rename_key": "charges.py:__extracted_func_0",
      "renameable": true,
      "parameters": [
...
        {
          "name": "_param_0",
          "kind": "value",
          "rename_key": "charges.py:__extracted_func_0._param_0",
          "bindings": [
            {
              "file": "charges.py",
              "line": 11,
              "expression": "0.10"
            },
...
```

The inventory lists each helper with its source, its calls, and each parameter
with the value every call passes: here `0.10`, `0.25` and `0.40`, which says the
parameter is a discount rate. Each `rename_key` is what a mapping names. Save
this as `renames.json`:

```json
{
  "charges.py:__extracted_func_0": "discounted_total",
  "charges.py:__extracted_func_0._param_0": "discount_rate"
}
```

`--preview` shows the change without making it:

```bash
towel rename-helpers named --rename-file renames.json --preview
```

```diff
  charges.py:__extracted_func_0 -> discounted_total
  charges.py:__extracted_func_0._param_0 -> discount_rate

--- before/charges.py
+++ after/charges.py
@@ -1,6 +1,6 @@
-def __extracted_func_0(amount, _param_0):
+def discounted_total(amount, discount_rate):
     base = max(amount, 0)
-    discount = base * _param_0
+    discount = base * discount_rate
     discounted = base - discount
     tax = discounted * 0.08
     shipping = 5 if discounted < 100 else 0
@@ -8,12 +8,12 @@


 def checkout_total(amount):
-    return __extracted_func_0(amount, 0.10)
+    return discounted_total(amount, 0.10)


 def member_total(amount):
-    return __extracted_func_0(amount, 0.25)
+    return discounted_total(amount, 0.25)


 def wholesale_total(amount):
-    return __extracted_func_0(amount, 0.40)
+    return discounted_total(amount, 0.40)

[PREVIEW] Would make 6 change(s)
```

Then apply it, and run your tests:

```bash
towel rename-helpers named --rename-file renames.json
```

```text
  charges.py:__extracted_func_0 -> discounted_total
  charges.py:__extracted_func_0._param_0 -> discount_rate

Applied 6 change(s)
```

## Asking an assistant for names

Without `--rename-file`, `rename-helpers` prints a prompt that shows each
helper, its rename keys and a call, and asks for a JSON mapping. Give it to an
AI assistant such as Claude or ChatGPT, paste the answer back, then press
Enter and Ctrl-D; Towel checks and applies it as above. Towel itself sends
nothing anywhere. The prompt is the same for every assistant, so leave out the
deprecated `--llm` option. Review the names before you keep them: a name is a
claim about what the code means.

## Rename keys

| Key | Renames |
| --- | --- |
| `path.py:helper` | A module-level helper, and its calls and imports. |
| `path.py:Class.__helper` | A helper method private to `Class`, and its calls in the class. |
| `path.py:helper.parameter` | A parameter of a module-level helper. |
| `path.py:Class.__helper.parameter` | A parameter of a helper method. |

Paths are relative to the target. When only one file has a helper of a given
name, the path can be left out (`"__extracted_func_0": "discounted_total"`), as
the quick start does. `--list` prints a helper method's name without its
class; take the exact key from `--list --json`. A helper method's new name
must start with two underscores, and not end with two, even where your style
prefers one: that keeps it private to its class, so a subclass cannot
override it by accident.

## Where to run it

Give the project's root, the directory with `pyproject.toml`, `setup.cfg` or
`setup.py`, or a package, so that Towel sees every file that calls the helpers.
Use `--file` and `--function` to rename only some helpers; Towel still checks
the rest of the project for callers. If the directory has no such marker, add
`--complete-scope` to declare that it holds every caller. A rename that would
need to change a file outside the target is refused. `--list` renames nothing, so
it needs no project root and works on any directory.

If you copied only one package out of a larger project, its imports may not
resolve in the copy. Put the reviewed files back in a checkout of the whole
project, at their original paths, and rename there.

## What can go wrong

The whole mapping is checked before anything is written, and applied as one
batch. A key that names no helper, a Python keyword, a name the helper already
uses anywhere in its body, even as an attribute (renaming a parameter to
`status` in a helper that reads `task.status` is refused), or a use of the
helper Towel cannot follow (such as `getattr(module, "__extracted_func_0")`)
refuses the batch: nothing changes, and the exit status is 2. `--json` reports
the result for a script. A mapping file that is not valid JSON is a different
failure: the exit status is 1, and the error goes to stderr with nothing on
stdout. Applying a mapping a second time is safe: each name is reported as
`(already applied)`, nothing changes, and the exit status is 0.

`.towel-helpers.json`, which `dry` writes into its output directory, gives the
inventory each call's code before and after the change. After `dry` writes a
single file, the same data is beside it as `OUTPUT.towel-helpers.json`.
`rename-helpers` reads `.towel-helpers.json` only from the directory you give it, so give the directory `dry` wrote:
after `towel dry src/pkg src/pkg`, that is `src/pkg`, not the project root.
Delete the file once the helpers are named; without it, renaming still works,
but the inventory shows no call-site changes. `--format` formats the files a
rename changes with the project's Ruff or Black, and exits 2 if that formatter is
not installed where Towel runs (the `format` extra installs both). Without
`--format`, Towel says which formatter command would restore the project's layout.

**Next:** [using Towel on a project](USING_TOWEL.md) covers testing and committing the result.

[Documentation index](README.md)
