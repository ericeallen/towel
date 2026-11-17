# Known limitations

[Documentation index](README.md)

Towel checks every change it makes against what your code does, but it reads
your code as text and structure; it cannot see code that looks at itself.
Save this as `audit.py`, whose functions record which function called them:

```python
import inspect


def record_login(user, log):
    caller = inspect.stack()[1].function
    log.append((caller, "login", user.name))
    log.append((caller, "seen", user.last_seen))
    return len(log)


def record_logout(user, log):
    caller = inspect.stack()[1].function
    log.append((caller, "logout", user.name))
    log.append((caller, "seen", user.last_seen))
    return len(log)
```

```bash
towel preview audit.py
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  audit.py: 1 refactoring(s)
    - Extract common code from record_login and record_logout
--- before/audit.py
+++ after/audit.py
@@ -1,15 +1,16 @@
 import inspect


-def record_login(user, log):
+def __extracted_func_0(user, log, _param_0):
     caller = inspect.stack()[1].function
-    log.append((caller, "login", user.name))
+    log.append((caller, _param_0, user.name))
     log.append((caller, "seen", user.last_seen))
     return len(log)


+def record_login(user, log):
+    return __extracted_func_0(user, log, "login")
+
+
 def record_logout(user, log):
-    caller = inspect.stack()[1].function
-    log.append((caller, "logout", user.name))
-    log.append((caller, "seen", user.last_seen))
-    return len(log)
+    return __extracted_func_0(user, log, "logout")
Input files were not changed.
```

After this change `caller` would name `record_login` or `record_logout`, not
the function that called them: `inspect.stack()` now sees one more frame.
Mark each function whose frame or source matters by ending its `def` line with
`# towel: no-extract`:

```python
def record_login(user, log):  # towel: no-extract
```

Towel then leaves that function's definition and body exactly as they are.
To leave a whole file or directory alone, pass `--exclude NAME`
([command reference](CLI_GUIDE.md#protect-a-function)).

## What Towel cannot see

Towel preserves the behavior of programs that do not use reflection or
self-instrumentation, including through the code they call. It does not look
for these, so it is up to you to protect them:

- **Reflection**: `inspect.stack()`, `sys._getframe()`, `locals()`, reading a
  function's source, bytecode or `__qualname__`, or listing a module's names
  (a new helper is a new name).
- **Self-instrumentation**: tools that read or rewrite a function's body, such
  as pytest's assertion rewriting, typeguard, numba and inline-snapshot.

## What Towel leaves alone

- **Code it cannot make smaller**, and code whose copies differ in more than
  values ([every reason](WHY_LEFT_ALONE.md)).
- **Code that yields.** A block with `yield` stays where it is.
- **Copies in different modules**, unless you pass `--cross-module`
  ([sharing across modules](CROSS_MODULE.md)).
- **Files it cannot read or parse.** A run skips them with a warning and says
  how many it skipped. With `--strict` it stops instead, and names the
  `--exclude` that would leave the file out.
- **Code under a formatter or linter directive** such as `# fmt: off` or
  `# pylint: disable`, and copies whose comments carry different directives.
- **Changes the type checker rejects**, in a project it can type-check
  ([typed projects](TYPING.md)).

By default Towel also assumes your program does not depend on exactly when
Python frees objects; [relaxed and strict](RELAXED_AND_STRICT.md) explains
that assumption and how to drop it.

## Resources and platform

Python 3.11 to 3.15 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis uses the `fork` start method;
under other start methods analysis runs on a single core. Windows is not a
supported or validated release platform.

Memory, not cores, limits the largest projects; set `TOWEL_WORKERS=1` on a
small machine. [Requirements](REQUIREMENTS.md) and [performance](PERFORMANCE.md)
give figures.

## The full specification

[The correctness model](design/CORRECTNESS_MODEL.md) states every check
Towel makes, every reason it declines, and what it does not model, for
contributors and for anyone auditing a result.
