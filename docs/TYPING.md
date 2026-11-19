# Typed projects

In a project with type annotations, Towel annotates the helpers it writes and
type-checks every change before it makes it. The example below needs mypy and
Black where Towel runs, which `uv tool install "code-towel[format,types]"`
provides; [without a checker](#which-checker-runs), the result differs. Save this
as `pyproject.toml`:

```toml
[project]
name = "inventory"
version = "0.1.0"
requires-python = ">=3.11"

[tool.mypy]
strict = true

[tool.black]
line-length = 88
```

Save this as `inventory/report.py`:

```python
from dataclasses import dataclass


@dataclass
class Item:
    name: str
    count: int
    price: float


def low_stock(items: list[Item]) -> list[str]:
    names = []
    for item in items:
        if item.count < 5:
            names.append(item.name.upper())
    names.sort()
    return names


def out_of_stock(items: list[Item]) -> list[str]:
    names = []
    for item in items:
        if item.count < 1:
            names.append(item.name.upper())
    names.sort()
    return names
```

```bash
towel preview inventory
```

```diff
Note: formatting generated code with Black (project configuration).
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  report.py: 1 refactoring(s)
    - Extract common code from low_stock and out_of_stock
--- before/report.py
+++ after/report.py
@@ -1,4 +1,13 @@
 from dataclasses import dataclass
+
+
+def __extracted_func_0(items: "list[Item]", __param_0: int, /) -> "list[str]":
+    names = []
+    for item in items:
+        if item.count < __param_0:
+            names.append(item.name.upper())
+    names.sort()
+    return names


 @dataclass
@@ -9,18 +18,8 @@


 def low_stock(items: list[Item]) -> list[str]:
-    names = []
-    for item in items:
-        if item.count < 5:
-            names.append(item.name.upper())
-    names.sort()
-    return names
+    return __extracted_func_0(items, 5)


 def out_of_stock(items: list[Item]) -> list[str]:
-    names = []
-    for item in items:
-        if item.count < 1:
-            names.append(item.name.upper())
-    names.sort()
-    return names
+    return __extracted_func_0(items, 1)
Input files were not changed.
```

The helper takes its annotations from the functions it came from: `items` is
declared `list[Item]` in both, and the threshold is an `int`. mypy checked
the changed module, with the project's `strict = true`, before Towel proposed
it.

## Run Towel where your project's dependencies are

A type checker can check only code whose imports it can resolve, so Towel
must run in an environment that has your project's dependencies. A copy
installed with `uv tool install` or `pipx` has an environment of its own, with
none of them. In a project that uv manages, run Towel inside the project's
environment instead:

```bash
uv run --with "code-towel[format,types]" towel preview src/
```

In any other virtual environment, install it there with
`pip install "code-towel[format,types]"` and run that environment's `towel`.

## Which checker runs

| The project configures | Towel uses |
| --- | --- |
| mypy | mypy |
| Pyright | Pyright |
| Both | mypy to infer types, both to check |
| Neither | whichever is installed where Towel runs, preferring mypy, with its defaults |

Without a working checker, what Towel writes depends on why:

| Situation | Helpers | `types` in `--json` |
| --- | --- | --- |
| No checker installed, none configured | Annotations copied from the call sites, not verified; a note says so | `"unverified"` |
| A configured checker is missing | No annotations, with a warning that names it and how to install it; `--strict` stops with status 1 instead | `"unverified"` |
| `--no-types` | No annotations, and no checker runs | `"off"` |

Configured mypy plugins, such as
django-stubs' or pydantic's, load as they do when you run mypy, and they run
code your project configures.

## What a checked change means

Before changing anything, Towel checks the project as it is. Errors already
there are allowed to stay; a change that adds a new error is left alone, with
the reason "refused by the type checker". Each accepted change becomes the
starting point for the next.

Towel cannot check what the checker cannot see:

- **An import the checker cannot type**, because the package or its stubs are
  missing: in relaxed mode a change to unannotated code there goes ahead, and
  the run says so once. A change to annotated code is left alone, naming the
  import, and so is every such change with `--strict`. Install the missing
  package or stubs where Towel runs.
- **Code the checker considers unreachable**, such as a branch for another
  platform or Python version, is left alone.
- **A checker that fails while checking the project as it is**, for example
  because a plugin cannot load: relaxed mode warns, says why, and goes on
  without type checking (`--json` reports `"types": "unverified"`); `--strict`
  stops before writing anything, with exit status 1. A checker that fails on a
  proposed change refuses that change.

## Reading a typed helper

- **Quoted annotations.** Without `from __future__ import annotations`, Towel
  quotes compound types such as `list[Item]` and `list[str]` in a helper.
  Even a subscript can run user code when Python evaluates it. A simple name
  such as `int` can stay bare if it is already bound where the helper is
  defined. Towel also quotes syntax too new for the project's oldest Python.
  With the future import, Python postpones evaluation, so both list types in
  this example can stay bare.
- **The `/`** makes the generated parameters positional-only, so a name such
  as `__param_0` is never passed by keyword. Towel adds it when
  `requires-python` allows. `rename-helpers` keeps it; delete it after
  renaming if you want callers to pass the arguments by keyword.
- **Imports under `if 0 > 1:`.** A name that only an annotation needs is
  imported under `if 0 > 1:`. Type checkers read the import; Python never runs
  it. Towel does not use `if TYPE_CHECKING:` because that needs an import of
  its own and tests a variable, which a program can change; `0 > 1` needs
  nothing and is always false.
- **Generic helpers.** Copies that work on different types can share a helper
  with a type parameter, such as `list[T] -> T`.

## Running without the checker

`--no-types` runs no checker and writes helpers without annotations; it keeps
the annotations already in your code. On a typed project it is several times
faster. Your own type check, run afterwards, then decides whether to keep the change.

Every rule Towel follows, including how existing errors are matched after
code moves and how narrowing is kept in the caller, is in
[type checking, in full](design/TYPE_CHECKING_RULES.md).

**Next:** [naming helpers](NAMING.md) replaces the generated names.

[Documentation index](README.md)
