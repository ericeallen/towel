# Relaxed and strict

[Documentation index](README.md)

Towel 2.0 has two modes. Relaxed mode, the default, makes a few assumptions
that nearly every program satisfies, and in return extracts far more code and
writes plain calls. Strict mode, `--strict`, makes none of them, as every
release before 2.0 did. Save this as `fetch.py`:

```python
async def fetch_user(client, user_id):
    response = await client.get(f"/users/{user_id}")
    response.raise_for_status()
    data = await response.json()
    return data["user"]


async def fetch_team(client, team_id):
    response = await client.get(f"/teams/{team_id}")
    response.raise_for_status()
    data = await response.json()
    return data["team"]
```

In relaxed mode, the copies become one `async` helper:

```bash
towel preview fetch.py
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  fetch.py: 1 refactoring(s)
    - Extract common code from fetch_user and fetch_team
--- before/fetch.py
+++ after/fetch.py
@@ -1,12 +1,13 @@
-async def fetch_user(client, user_id):
-    response = await client.get(f"/users/{user_id}")
+async def __extracted_func_0(client, _param_0, _param_1, _param_2):
+    response = await client.get(f"/{_param_0}s/{_param_1}")
     response.raise_for_status()
     data = await response.json()
-    return data["user"]
+    return data[_param_2]
+
+
+async def fetch_user(client, user_id):
+    return await __extracted_func_0(client, "user", user_id, "user")


 async def fetch_team(client, team_id):
-    response = await client.get(f"/teams/{team_id}")
-    response.raise_for_status()
-    data = await response.json()
-    return data["team"]
+    return await __extracted_func_0(client, "team", team_id, "team")
Input files were not changed.
```

In strict mode, Towel leaves them alone:

```bash
towel preview fetch.py --strict
```

```text
Would apply 0 refactoring(s).
Analysed 1 Python file (2 functions).
Found 3 places with similar code and extracted none.

Why code was left alone (most frequent first; --explain locates each place):
  3  Moving the block would change the order in which its function releases objects when it returns: the function's arguments or other variables on one side, variables the block assigns on the other. [owned_binding_frame_boundary]

Places left alone, largest first:
  fetch.py:2-5 and fetch.py:9-12: Moving the block would change the order in which its function releases objects when it returns: the function's arguments or other variables on one side, variables the block assigns on the other.
  fetch.py:2-4 and fetch.py:9-11: Moving the block would change the order in which its function releases objects when it returns: the function's arguments or other variables on one side, variables the block assigns on the other.
  fetch.py:3-5 and fetch.py:10-12: Moving the block would change the order in which its function releases objects when it returns: the function's arguments or other variables on one side, variables the block assigns on the other.
Input files were not changed.
```

`response` and `data` live until `fetch_user` returns. In a helper they would
be released when the helper returns, a moment earlier, and in a different
order from `client` and `user_id`. No ordinary program can tell. Strict mode
assumes yours might.

## What relaxed mode assumes

Relaxed mode keeps what your program computes and does: its return values,
output and exceptions, and the order of the side effects its calls perform. It
assumes three things that strict mode does not:

1. **Your program does not depend on when Python frees an object it no longer
   uses**, or in what order. Python's own language reference leaves that to
   the implementation, and CPython 3.14 changed the order. This matters only
   to code with `__del__` methods, weak-reference callbacks or garbage
   collector hooks whose timing is part of what the program does.
2. **Reading an attribute, indexing, comparing and arithmetic have no side
   effects**, and testing a value's truth gives the same answer twice. They may
   still fail, and where they fail is kept.
3. **A module's functions, classes and imports are not rebound while a helper
   runs**, as a test's monkeypatch can do.

Relaxed mode also lets a lambda that uses only its own parameters move into a
helper, which changes the lambda's `__qualname__`.

One kind of object is protected even in relaxed mode: one that cleans up
something the program can still reach another way. A temporary directory, an
open file or socket, a process pool, a memory map or a weak reference created
in a block keeps working after extraction, or the block is left alone with
the reason `resource_lifetime_unproven`. A `with` statement around the
resource always works.

## When to use `--strict`

Use it when your program, or its tests, observe when objects are freed: a
`__del__` that does work others wait for, a weak-reference callback, a
`gc.callbacks` hook, a test that checks that a file is closed or an object
collected at a precise point. When Towel sees such code in the project, a
relaxed run says so before anything else. Save this as `handles.py`:

```python
class Handle:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        print("closing", self.name)
```

```bash
towel preview . --summary
```

```text
Note: relaxed mode assumes exact object lifetimes and function lookup timing are not observed; --strict (or strict = true under [tool.towel]) preserves them. Found handles.py:5: Handle.__del__.
Would apply 1 refactoring(s).
Analysed 2 Python files (4 functions).
Would change 1 file(s):
  fetch.py: 1 refactoring(s)
    - Extract common code from fetch_user and fetch_team
Input files were not changed.
```

The note is a hint, not a verdict: here `Handle` has nothing to do with
`fetch.py`, and the extraction is safe. Read the places it names and decide.
`--quiet` hides the note; `--json` reports it in `relaxed_findings`.

To make strict mode a project's default, add it to `pyproject.toml`
([recipes](RECIPES.md#project-settings)):

```toml
[tool.towel]
strict = true
```

`--no-strict` then selects relaxed mode for one run.

## What strict mode writes

Strict mode extracts less, and where a whole function body moves, it keeps
each argument alive exactly as long as before. Save this as `prices.py`, the
quick start's code:

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

```bash
towel preview prices.py --strict
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (3 functions).
Would change 1 file(s):
  prices.py: 1 refactoring(s)
    - Extract common code from checkout_total and member_total
--- before/prices.py
+++ after/prices.py
@@ -1,25 +1,25 @@
-def checkout_total(amount):
+def __extracted_func_0(amount, _param_0, _towel_owner):
     base = max(amount, 0)
-    discount = base * 0.10
+    discount = base * _param_0
     discounted = base - discount
     tax = discounted * 0.08
     shipping = 5 if discounted < 100 else 0
     return round(discounted + shipping + tax, 2)


+def checkout_total(amount):
+    _towel_arguments = [(amount,)]
+    del amount
+    return __extracted_func_0(_towel_arguments[0][0], 0.10, _towel_arguments.pop())
+
+
 def member_total(amount):
-    base = max(amount, 0)
-    discount = base * 0.25
-    discounted = base - discount
-    tax = discounted * 0.08
-    shipping = 5 if discounted < 100 else 0
-    return round(discounted + shipping + tax, 2)
+    _towel_arguments = [(amount,)]
+    del amount
+    return __extracted_func_0(_towel_arguments[0][0], 0.25, _towel_arguments.pop())


 def wholesale_total(amount):
-    base = max(amount, 0)
-    discount = base * 0.40
-    discounted = base - discount
-    tax = discounted * 0.08
-    shipping = 5 if discounted < 100 else 0
-    return round(discounted + shipping + tax, 2)
+    _towel_arguments = [(amount,)]
+    del amount
+    return __extracted_func_0(_towel_arguments[0][0], 0.40, _towel_arguments.pop())
Input files were not changed.
```

The caller hands its argument to the helper and deletes its own reference;
the helper's last parameter, `_towel_owner`, holds it until the helper
returns, which is when the caller would have released it. Relaxed mode writes
`return __extracted_func_0(amount, 0.10)`.

Strict mode's guarantees about release order are proved for CPython 3.11 to
3.13. CPython 3.14 releases a function's variables in the opposite order, so
on 3.14 and later strict mode leaves alone what those proofs would extract,
with the reason `lifetime_order_uncertified`; the examples above show 3.13.
Relaxed mode behaves the same on every version.
