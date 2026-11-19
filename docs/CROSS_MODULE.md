# Sharing helpers across modules

By default Towel shares a helper only within one module. With `--cross-module`
it also shares one between modules: the helper goes into one of them and the
others import it. In this project, which configures Black with Black installed,
`shop/invoices.py` imports `shop.orders` and repeats the calculation in
`order_total`:

```bash
towel preview shop/ --cross-module
```

```diff
Note: formatting generated code with Black (project configuration).
Would apply 2 refactoring(s).
Analysed 3 Python files (3 functions).
Would change 2 file(s):
  invoices.py: 1 refactoring(s)
    - Extract common code from invoice_total (invoices.py) and order_total (orders.py)
  orders.py: 1 refactoring(s)
    - Extract common code from invoice_total (invoices.py) and order_total (orders.py)
--- before/invoices.py
+++ after/invoices.py
@@ -1,11 +1,9 @@
 import shop.orders
+from shop.orders import __extracted_func_0


 def invoice_total(invoice):
-    subtotal = invoice.amount_due
-    tax = round(subtotal * 0.08, 2)
-    shipping = 0 if subtotal >= 100 else 5
-    return round(subtotal + tax + shipping, 2)
+    return __extracted_func_0(invoice.amount_due)


 def reorder(invoice):
--- before/orders.py
+++ after/orders.py
@@ -1,5 +1,9 @@
-def order_total(order):
-    subtotal = order.subtotal
+def __extracted_func_0(__param_0):
+    subtotal = __param_0
     tax = round(subtotal * 0.08, 2)
     shipping = 0 if subtotal >= 100 else 5
     return round(subtotal + tax + shipping, 2)
+
+
+def order_total(order):
+    return __extracted_func_0(order.subtotal)
Input files were not changed.
```

The helper went into `shop/orders.py`, which `invoices.py` already imports, so
the new import loads nothing that was not loaded before. The run counts one
refactoring for each file it changes, so one shared helper is two here.

## When modules share nothing

Towel adds an import between two modules only when one of them already
imports the other, unconditionally, at its top. Otherwise an import could run
code earlier, or in a different order, than before. Save this as
`shop/invoices.py`, which no longer imports `shop.orders`:

```python
def invoice_total(invoice):
    subtotal = invoice.amount_due
    tax = round(subtotal * 0.08, 2)
    shipping = 0 if subtotal >= 100 else 5
    return round(subtotal + tax + shipping, 2)
```

```bash
towel preview shop/ --cross-module --summary
```

```text
Would apply 0 refactoring(s).
...
--cross-module shared nothing between modules. What each pair of modules lacked:
  invoices.py and orders.py: shop.orders does not import shop.invoices at its top, and loading shop.invoices there would replace the attribute invoices of package shop.
A module can share a helper with another only if one of them already imports the other, unconditionally, at its top, before its first definition; the helper then goes into the module imported. Adding that import is the one change that lets Towel share such code.
Input files were not changed.
```

Add the import if the modules belong together, and run Towel again. Towel
also shares nothing between modules of different distributions (directories
with their own `pyproject.toml`, `setup.cfg` or `setup.py`). A module the
build ships never borrows a helper from one the build leaves out, or from one
that only some platforms or Python versions can import.

## After a cross-module run

- **Imports below the helper.** A helper goes before the host module's other
  imports, so that it exists even while the module is still being imported.
  Ruff and flake8 then report those imports as E402, "module level import not
  at top of file". If no module imports the host back while it loads, move the
  helper below the imports; otherwise add `# noqa: E402` to them.
- **Builtins and modules read directly.** In relaxed mode, the default, a
  shared helper reads a builtin such as `len`, or a module both sides import
  the same way, directly, unless the project could rebind that name. Code
  outside the project that rebinds one goes unseen, and equal literals move
  into the helper, so comparing them with `is` can give a different answer. A
  program that does either needs `--strict`, which passes an imported module in
  as an argument and keeps each literal in its own module. Strict mode leaves
  alone a block that reads a builtin across modules, unless you pass
  `--parameterize-builtins`, which hands each caller's builtin to the helper in
  either mode.
- **Python API.** `cross_module_helpers=True` turns the feature on for an
  engine ([Python API](USAGE_GUIDE.md)).

## Resolving import refusals

A run that cannot tell which file an import names, or which copy of a module
the program runs, refuses to share that module's code (`unproven_import`), and
before it starts it names each such problem with its remedy. The commonest is a
second copy in the tree, such as a stale `build/lib/alpha` beside `src/alpha`:
leave out the directory holding it with `--exclude`, or fix the import.
[Import refusals and their remedies](design/CROSS_MODULE_RULES.md#import-refusals-and-their-remedies)
lists the others.

Every rule Towel follows, including the build tools and dependency files it
reads, is in [cross-module sharing, in full](design/CROSS_MODULE_RULES.md).

**Next:** [typed projects](TYPING.md) covers checking a shared helper.

[Documentation index](README.md)
