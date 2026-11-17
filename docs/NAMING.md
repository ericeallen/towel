# Naming helpers

[Documentation index](README.md)

Towel can tell that code repeats, not what it means, so it names helpers
`__extracted_func_0` and the values that differ `_param_0`. `towel rename-helpers`
gives them real names, and updates every call, in one checked step. This page
starts from the [quick start](QUICKSTART.md)'s project, `demo`, and its
refactored copy:

```bash
towel dry demo cleaned --no-interactive
```

`--list --json` gives the full inventory:

```bash
towel rename-helpers cleaned --list --json
```

```json
{
  "target": "cleaned",
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
towel rename-helpers cleaned --rename-file renames.json --preview
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

Then apply it, and run the tests:

```bash
towel rename-helpers cleaned --rename-file renames.json
python3 -m unittest discover -s cleaned
```

```text
  charges.py:__extracted_func_0 -> discounted_total
  charges.py:__extracted_func_0._param_0 -> discount_rate

Applied 6 change(s)
.
----------------------------------------------------------------------
Ran 1 test in 0.000s

OK
```

## Asking an assistant for names

Without `--rename-file`, `rename-helpers` prints a prompt that shows each
helper, its rename keys and a call, and asks for a JSON mapping. Give it to an
AI assistant such as Claude or ChatGPT, paste the answer back, then press
Enter and Ctrl-D; Towel checks and applies it as above. Towel itself sends
nothing anywhere. Review the names before you keep them: a name is a claim
about what the code means.

## Rename keys

| Key | Renames |
| --- | --- |
| `path.py:helper` | A module-level helper, and its calls and imports. |
| `path.py:Class.__helper` | A helper method private to `Class`, and its calls in the class. |
| `path.py:helper.parameter` | A parameter of a module-level helper. |
| `path.py:Class.__helper.parameter` | A parameter of a helper method. |

Paths are relative to the target. When only one file has a helper of a given
name, the path can be left out (`"__extracted_func_0": "discounted_total"`), as
the quick start does. A helper method's new name must also start with two
underscores, and not end with two: that keeps it private to its class, so a
subclass cannot override it by accident.

## Where to run it

Give the project's root, the directory with `pyproject.toml`, `setup.cfg` or
`setup.py`, or a package, so that Towel sees every file that calls the helpers.
Use `--file` and `--function` to rename only some helpers; Towel still checks
the rest of the project for callers. If the directory has no such marker, add
`--complete-scope` to declare that it holds every caller. A rename that would
need to change a file outside the target is refused.

If you copied only one package out of a larger project, its imports may not
resolve in the copy. Put the reviewed files back in a checkout of the whole
project, at their original paths, and rename there.

## What can go wrong

The whole mapping is checked before anything is written, and applied as one
batch. A key that names no helper, a name that collides with another or is a
Python keyword, or a use of the helper Towel cannot follow (such as
`getattr(module, "__extracted_func_0")`) refuses the batch: nothing changes,
and the exit status is 2. `--json` reports the result for a script.

`.towel-helpers.json`, which `dry` writes beside its output, gives the
inventory each call's code before and after the change. Delete it once the
helpers are named; without it, renaming still works, but the inventory shows no
call-site changes. `--format` formats the files a rename changes with the
project's Ruff or Black; without it, Towel says which formatter command would
restore the project's layout.
