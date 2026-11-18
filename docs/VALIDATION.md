# How Towel is validated

This page describes the checks a release must pass. The results of a particular
release, with its versions and counts, are in that release's notes.

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

Each release is held to that promise by these checks, all of which must pass
before it is published:

| Check | What it does |
| --- | --- |
| The test suite | About 15,000 tests collected, on every supported Python (3.11 to 3.15) on Linux, and a subset on macOS. |
| Hostile cases | 406 small programs written to trip a refactoring tool up (328 single files and 78 projects): side-effect order, closures, `try` and `finally`, generators, class privacy, imports. Each runs before and after Towel, and its output must not change, except where a case records a difference that reflection or a relaxed-mode assumption permits. |
| Expected outputs | 28 example modules, and cross-module example projects, whose refactored output is compared with a reviewed copy, in relaxed and strict mode. |
| Fuzzing | Two families of 2,000 generated programs each, from new random seeds every release, run before and after refactoring. |
| Real projects | 141 published open-source projects, from Click and Rich to Sphinx. Towel runs on each, and the project's own test suite must give the same results afterwards. Towel need not change every project: five are reviewed cases where it must refuse for a recorded reason. A test result that relaxed mode changes passes only when a review traces it to a named assumption and the project's tests still pass in strict mode. |
| Itself | Towel refactors its own source, and its own tests then pass. |
| The installed package | The built wheel and sdist are installed into clean environments, with and without the optional tools, and exercised there. |

Every defect found, by these checks or by a person, becomes a test that
fails before the fix, so that it cannot return.

Each release's results, with the exact versions, counts and conditions, are
in its [release notes](https://github.com/ericeallen/towel/releases) and the
[production readiness report](maintainers/PRODUCTION_READINESS.md). The
[release procedure](maintainers/RELEASING.md) gives every step. To see what
Towel did on code like yours, see the [gallery](GALLERY.md), and run your own
tests on the result: they know your program better than any of these checks.

[Documentation index](README.md)
