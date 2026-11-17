# Reading Towel's output

[Documentation index](README.md)

Most of what Towel writes is a helper and calls to it, with the values that
differed between the copies passed as arguments, as in the
[quick start](QUICKSTART.md). This page explains the rest: a `lambda` in a
call, a helper inside a class, a call to a function you already had, and what
strict mode adds. Every example runs in a project whose `pyproject.toml`
configures Black, so the inserted code is formatted.

## A `lambda:` that delays an argument

Save this as `notify.py`:

```python
def notify_owner(order, mailer):
    mailer.connect()
    mailer.send(order.owner_email, f"Order {order.id} shipped")
    mailer.close()
    return order.id


def notify_buyer(order, mailer):
    mailer.connect()
    mailer.send(order.buyer.email(), f"Order {order.id} shipped")
    mailer.close()
    return order.id
```

```bash
towel preview notify.py
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  notify.py: 1 refactoring(s)
    - Extract common code from notify_owner and notify_buyer
--- before/notify.py
+++ after/notify.py
@@ -1,12 +1,13 @@
-def notify_owner(order, mailer):
+def __extracted_func_0(order, mailer, _param_0):
     mailer.connect()
-    mailer.send(order.owner_email, f"Order {order.id} shipped")
+    mailer.send(_param_0(), f"Order {order.id} shipped")
     mailer.close()
     return order.id


+def notify_owner(order, mailer):
+    return __extracted_func_0(order, mailer, lambda: order.owner_email)
+
+
 def notify_buyer(order, mailer):
-    mailer.connect()
-    mailer.send(order.buyer.email(), f"Order {order.id} shipped")
-    mailer.close()
-    return order.id
+    return __extracted_func_0(order, mailer, lambda: order.buyer.email())
Input files were not changed.
```

The copies differ in the address: `order.owner_email` in one, a call to
`order.buyer.email()` in the other. In the original, that expression runs after
`mailer.connect()`. Passed as a plain argument it would run first, before the
connection, and if `email()` failed the mailer would no longer be connected
when it did. So Towel passes `lambda: order.buyer.email()`, and the helper
calls `_param_0()` exactly where the expression used to be.

A `lambda:` appears only where it is needed: when the expression runs after
something else in the block, runs repeatedly, runs only under a condition, or
runs inside a `try` that could catch its failure. Constants, variables,
module-level functions and classes, and expressions the helper evaluates first
are passed as themselves. If one call needs a `lambda:` for a parameter, every
call passes one for it.

## A `lambda` that takes an argument

Save this as `weights.py`:

```python
def total_weight(items):
    total = 0
    for item in items:
        total += item.weight_kg
    print(f"{len(items)} items")
    return round(total, 1)


def total_volume(items):
    total = 0
    for item in items:
        total += item.volume_l
    print(f"{len(items)} items")
    return round(total, 1)
```

```bash
towel preview weights.py
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  weights.py: 1 refactoring(s)
    - Extract common code from total_weight and total_volume
--- before/weights.py
+++ after/weights.py
@@ -1,14 +1,14 @@
-def total_weight(items):
+def __extracted_func_0(items, _param_0):
     total = 0
     for item in items:
-        total += item.weight_kg
+        total += _param_0(item)
     print(f"{len(items)} items")
     return round(total, 1)


+def total_weight(items):
+    return __extracted_func_0(items, lambda item: item.weight_kg)
+
+
 def total_volume(items):
-    total = 0
-    for item in items:
-        total += item.volume_l
-    print(f"{len(items)} items")
-    return round(total, 1)
+    return __extracted_func_0(items, lambda item: item.volume_l)
Input files were not changed.
```

The attribute that differs belongs to `item`, a variable of the loop inside the
helper, so no caller can compute it in advance. Each caller passes a function
of the loop variable instead: `lambda item: item.weight_kg`.

## A helper inside a class

Save this as `cart.py`:

```python
class Cart:
    def __init__(self, items):
        self.items = items

    def subtotal(self):
        total = 0
        for item in self.items:
            total += item.price * item.quantity
        return round(total, 2)

    def taxed_total(self, rate):
        total = 0
        for item in self.items:
            total += item.price * item.quantity
        return round(total * (1 + rate), 2)
```

```bash
towel preview cart.py
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (3 functions).
Would change 1 file(s):
  cart.py: 1 refactoring(s)
    - Extract common code from subtotal and taxed_total
--- before/cart.py
+++ after/cart.py
@@ -3,13 +3,15 @@
         self.items = items

     def subtotal(self):
+        total = self.__extracted_func_0()
+        return round(total, 2)
+
+    def taxed_total(self, rate):
+        total = self.__extracted_func_0()
+        return round(total * (1 + rate), 2)
+
+    def __extracted_func_0(self):
         total = 0
         for item in self.items:
             total += item.price * item.quantity
-        return round(total, 2)
-
-    def taxed_total(self, rate):
-        total = 0
-        for item in self.items:
-            total += item.price * item.quantity
-        return round(total * (1 + rate), 2)
+        return total
Input files were not changed.
```

Code shared by methods of one class becomes a method of that class. Its name
starts with two underscores, so Python makes it private to the class, and a
subclass cannot override it by accident. The helper returns `total`, the one
variable the callers go on to use. Code shared by methods of different classes
becomes a module-level function that takes the object as an argument.

## A call to a function you already have

Save this as `shipping.py`:

```python
def can_ship(order):
    return (
        order.weight_kg <= 30
        and order.country in ("US", "CA")
        and not order.on_hold
    )


def ship_all(orders):
    for order in orders:
        if (
            order.weight_kg <= 30
            and order.country in ("US", "CA")
            and not order.on_hold
        ):
            order.ship()
```

```bash
towel preview shipping.py
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  shipping.py: 1 refactoring(s)
    - Reuse can_ship (shipping.py) for duplicated code in ship_all
--- before/shipping.py
+++ after/shipping.py
@@ -8,9 +8,5 @@

 def ship_all(orders):
     for order in orders:
-        if (
-            order.weight_kg <= 30
-            and order.country in ("US", "CA")
-            and not order.on_hold
-        ):
+        if can_ship(order):
             order.ship()
Input files were not changed.
```

When one copy is already the whole body of a function, the other copies call
that function, and there is no new helper to name. The copy here is an
expression, not a block of statements: a repeated expression of at least
`--min-lines` lines is a candidate too.

## Names, parameters and other details

- **Names.** Helpers are called `__extracted_func_0`, `__extracted_func_1` and so
  on; parameters keep the name both copies used, or get `_param_0` or
  `__param_0` where the copies differ. Rename them with `towel rename-helpers`
  ([naming guide](NAMING.md)).
- **A `/` in the signature** makes the parameters before it positional-only, so a
  generated name such as `__param_0` can never be passed by keyword. You see it
  when the project's `requires-python` allows it.
- **`async def` helpers.** A block that uses `await`, `async for` or `async with`
  becomes an `async def` helper, called as `await helper(…)`. A block that
  yields is left alone.
- **Where helpers go.** A module's helper goes before its first definition; a
  helper of one class or function goes inside it. With `--cross-module`, one
  module defines a helper and the others import it ([sharing across modules](CROSS_MODULE.md)).
- **Imports under `if 0 > 1:`.** In a typed project, an import that only an
  annotation needs is added under `if 0 > 1:`, which never runs; type checkers
  still read it ([typed projects](TYPING.md)).
- **Comments and formatting.** Comments in moved code move with it. With Ruff or
  Black configured, and installed where Towel runs, inserted code is formatted;
  otherwise Towel says so in a note and leaves layout alone. `--no-format`
  turns formatting off.
- **Small duplicates stay.** A helper must be smaller than the copies it
  replaces, counting the helper and every call, so a short pair is often left
  alone ([why](WHY_LEFT_ALONE.md)).
- **`--max-parameters`** (default 5) counts the differences that become
  parameters. Variables the copies read are passed as further arguments and do
  not count, except in strict mode when a whole function body moves: there the
  whole signature must fit, including the hidden `_towel_owner` parameter.
- **`--parameterize-builtins`.** Copies in different modules may not mean the same
  `len`: a module can define its own. Such copies are left alone unless you pass
  `--parameterize-builtins`, which hands the helper each caller's `len` as
  `lambda: len`. They are still left alone where the project could replace a
  module's `__builtins__`, and where the copies use different builtins.

## Strict mode's bookkeeping

With `--strict`, Towel keeps the exact moment Python releases each object, as
releases before 2.0 did ([relaxed and strict](RELAXED_AND_STRICT.md)). Where a
whole function body moves, the callers then look like this:

```python
def checkout_total(amount):
    _towel_arguments = [(amount,)]
    del amount
    return __extracted_func_0(_towel_arguments[0][0], 0.10, _towel_arguments.pop())
```

Python keeps a function's arguments alive until it returns. These lines hand
`amount` to the helper, whose last parameter, `_towel_owner`, holds it, so it
is released when the helper returns, as it was before. A name such as
`_towel_keep_0` that the caller assigns and never reads does the same for a
variable the block created. Relaxed mode writes none of this.

## What Towel guarantees

Towel preserves the behavior of programs that do not use reflection or
self-instrumentation, including through code they call: a change that alters
such a program is a bug. Code that reads its own source, bytecode, frames or
namespace, and tools that rewrite functions (pytest's assertion rewriting,
typeguard, numba), can see the difference. Protect such a function by ending
its `def` line with `# towel: no-extract`:

```python
def frame_sensitive(value):  # towel: no-extract
    return inspect_caller(value)
```

or leave its file out with `--exclude`
([command reference](CLI_GUIDE.md#protect-a-function)). The complete rules
behind every form on this page are in
[generated code, in full](design/GENERATED_CODE_RULES.md).
