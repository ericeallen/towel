# How Towel is validated

This page describes the checks a release must pass. The results of a particular
release, with its versions and counts, are in that release's notes.

**What Towel promises.** A change Towel makes preserves the program's results,
output, exceptions and order of side effects. This holds for programs that do
not use reflection or self-instrumentation, including through code they call.
Examples include reading frames, source or line numbers with `inspect.stack()`,
attributing a warning to a caller with `warnings.warn(..., stacklevel=...)`,
logging the caller with `%(funcName)s` and rewriting functions as pytest does
when it rewrites assertions. Mark functions that depend on these behaviors with
`# towel: no-extract`.

In relaxed mode, the default, the program must tolerate changes in when Python
frees objects it no longer uses. Attribute reads, indexing, comparisons,
arithmetic and truth tests must have no side effects, and repeated truth tests
must give the same answer. A module's functions, classes and imports must keep
their bindings while a helper runs. If a lambda uses only its own parameters, the
program must tolerate a change to its `__qualname__` when Towel moves it.
With `--cross-module`, code outside
the project must leave each module's builtin bindings alone, and the program
must not depend on the identity of equal literals from different modules.
`--strict` removes these relaxed-mode assumptions. A change that breaks a
program meeting these conditions is a bug.

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
