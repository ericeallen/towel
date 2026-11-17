# Known limitations

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
Note: formatting generated code with Black (project configuration).
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

Towel does not look for reflection or self-instrumentation, so it is up to you
to protect code that uses them:

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

[Relaxed and strict](RELAXED_AND_STRICT.md) explains relaxed mode's three
assumptions and how to drop them.

## Resources and platform

Python 3.11 to 3.15 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis forks worker processes, as macOS
and Linux both allow; where Python cannot fork, analysis runs on a single core. Windows is not a
supported or validated release platform.

Memory, not cores, limits the largest projects; set `TOWEL_WORKERS=1` on a
small machine. [Requirements](REQUIREMENTS.md) and [performance](PERFORMANCE.md)
give figures.

## The full specification

[The correctness model](design/CORRECTNESS_MODEL.md) states every check
Towel makes, every reason it declines, and what it does not model, for
contributors and for anyone auditing a result.

[Documentation index](README.md)
