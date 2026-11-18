# Relaxed and strict

The same extraction, as each mode writes the caller:

```python
# Relaxed mode, the default
def checkout_total(amount):
    return __extracted_func_0(amount, 0.10)


# Strict mode, --strict
def checkout_total(amount):
    _towel_arguments = [(amount,)]
    del amount
    return __extracted_func_0(_towel_arguments[0][0], 0.10, _towel_arguments.pop())
```

Towel 2.0 has two modes. Relaxed mode, the default, makes three assumptions
that nearly every program satisfies, and in return extracts far more code and
writes plain calls. Strict mode, `--strict`, makes none of them, as every
release before 2.0 did.

**On Python 3.14 and later, strict mode extracts less.** CPython 3.14 changed
the order in which a function's variables are freed, and strict mode's proofs
about that order cover CPython 3.11 to 3.13. On 3.14 and later it leaves alone
whatever depends on that order, with the reason `lifetime_order_uncertified`;
the strict examples on this page show 3.13, with Black installed. If you will use `--strict`,
install Towel on 3.13: `uv tool install --python 3.13 code-towel`. Relaxed
mode behaves the same on every version.

## Which to use

**What Towel promises.** A change Towel makes keeps what your program
computes and does: its results, output, exceptions and the order of its side
effects. That holds for programs that do not use reflection or
self-instrumentation, including through code they call. Reflection here means
reading frames, source or line numbers, as `inspect.stack()` does, or a log
format with `%(funcName)s`; self-instrumentation means a tool that rewrites
functions, such as pytest's assertion rewriting. Mark a function that depends
on these with `# towel: no-extract`. In relaxed mode, the default, Towel also
assumes that your program does not depend on when Python frees an object it no
longer uses; that reading an attribute, indexing, comparing and arithmetic have
no side effects; and that a module's functions, classes and imports are not
rebound while a helper runs, as a test's monkeypatch can do. `--strict` makes
none of these three assumptions. A change that breaks a program meeting these
conditions is a bug.

Relaxed mode's assumptions reach a little further than that summary. Testing a
value's truth (`__bool__` or `__len__`) must have no side effects and give the
same answer twice. Nothing may depend on the `__qualname__` of a lambda that
uses only its own parameters. With `--cross-module`, code outside the project
must not rebind a builtin in one module, and equal literals must not be
compared with `is`. `--strict` assumes none of these.

Use `--strict` when your program, or its tests, depend on any of these:

- **When objects are freed**: a `__del__` that does work others wait for, a
  weak-reference callback, a `gc.callbacks` hook, or a test that checks that a
  file is closed or an object collected at a precise point.
- **Side effects of reading**: a property, `__getattr__`, `__getitem__`,
  `__eq__`, `__bool__` or arithmetic method that logs, counts, or changes
  state, so that calling it once more, or once less, would show.
- **Rebinding while code runs**: a module's function, class or imported name
  replaced while the code that calls it is running, as a monkeypatch inside a
  callback, or another thread, can do.
- **Resources your own code builds**: a temporary directory, file or socket
  that a function of yours creates and returns
  ([below](#resources-relaxed-mode-protects)).

Otherwise, use the default. When Towel sees code that may depend on when
objects are freed, a relaxed run says so in a note as it starts
([below](#the-notice-relaxed-mode-prints)).

## The two modes on one example

Save this as `fetch.py`:

```python
async def fetch_user(client, user_id):
    response = await client.get(f"/user/{user_id}")
    response.raise_for_status()
    data = await response.json()
    return data["profile"]


async def fetch_team(client, team_id):
    response = await client.get(f"/team/{team_id}")
    response.raise_for_status()
    data = await response.json()
    return data["members"]
```

In relaxed mode, the copies become one `async` helper:

```bash
towel preview fetch.py
```

```diff
Note: formatting generated code with Black (project configuration).
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  fetch.py: 1 refactoring(s)
    - Extract common code from fetch_user and fetch_team
--- before/fetch.py
+++ after/fetch.py
@@ -1,12 +1,13 @@
-async def fetch_user(client, user_id):
-    response = await client.get(f"/user/{user_id}")
+async def __extracted_func_0(client, _param_0, _param_1, _param_2):
+    response = await client.get(f"/{_param_0}/{_param_1}")
     response.raise_for_status()
     data = await response.json()
-    return data["profile"]
+    return data[_param_2]
+
+
+async def fetch_user(client, user_id):
+    return await __extracted_func_0(client, "user", user_id, "profile")


 async def fetch_team(client, team_id):
-    response = await client.get(f"/team/{team_id}")
-    response.raise_for_status()
-    data = await response.json()
-    return data["members"]
+    return await __extracted_func_0(client, "team", team_id, "members")
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
...
Input files were not changed.
```

`response` and `data` live until `fetch_user` returns. In a helper they would
be released when the helper returns, a moment earlier, and in a different
order from `client` and `user_id`. No ordinary program can tell. Strict mode
assumes yours might.

## What relaxed mode assumes, in detail

Relaxed mode keeps what your program computes and does: its return values,
output and exceptions, and the order of the side effects its calls perform.
These are its three assumptions:

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
helper, which changes the lambda's `__qualname__`; only reflection can see it.

## Resources relaxed mode protects

Some objects clean up something the program can still reach another way: a
temporary directory disappears when its object is freed, even if the program
still holds the path. When a block calls a constructor Towel recognizes, the
helper hands the object back so that it stays alive, or the block is left alone
with the reason `resource_lifetime_unproven`. The list covers `tempfile`'s
temporary files and directories, `open`, sockets, `multiprocessing` pools,
`mmap.mmap` and the `weakref` module's references
([the exact list](design/DECISIONS.md#2026-11-14-relaxed-mode-is-the-default-strict-mode-keeps-exact-lifetimes)).
A `with` statement inside the block also keeps the resource safe.

The protection is a list of constructors, not a proof. Towel does not know what
your own function returns:

```python
def make_dir():
    return tempfile.TemporaryDirectory()
```

If a block calls `make_dir()` and only the path is used after it, the helper's
return frees the directory, and the later use raises `FileNotFoundError`. The
same goes for a weak container Towel does not recognize. For code like this, use
`--strict`, or have the code after the block read the object itself, so that
the helper returns it.

## The notice relaxed mode prints

When Towel sees code in the project that may depend on when objects are
freed, a relaxed run says so in a note as it starts. Save this as `handles.py`:

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
Note: formatting generated code with Black (project configuration).
Would apply 1 refactoring(s).
Analysed 2 Python files (4 functions).
Would change 1 file(s):
  fetch.py: 1 refactoring(s)
    - Extract common code from fetch_user and fetch_team
Input files were not changed.
```

The note is a hint, not a verdict: here `Handle` has nothing to do with
`fetch.py`, and the extraction is safe. Read the places it names and decide.
`--quiet` hides the note; `--json` reports it in `relaxed_findings`. In a
project with a `[tool.towel]` table, the line naming that table comes first, and
`--quiet` does not hide it.

## Making strict the default

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
Note: formatting generated code with Black (project configuration).
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

**Next:** [reading Towel's output](GENERATED_CODE.md) explains the code each mode writes.

[Documentation index](README.md)
