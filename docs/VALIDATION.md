# How Towel is validated

[Documentation index](README.md)

Towel's promise is that a change it makes never alters what your program
does. Each release is held to it by these checks, all of which must pass
before it is published:

| Check | What it does |
| --- | --- |
| The test suite | About 11,700 tests, on every supported Python (3.11 to 3.15) on Linux, and a subset on macOS. |
| Hostile cases | 406 small programs written to trip a refactoring tool up: side-effect order, closures, `try` and `finally`, generators, class privacy, imports. Each runs before and after Towel, and its output must not change. |
| Expected outputs | 28 example modules, and cross-module example projects, whose refactored output is compared with a reviewed copy, in relaxed and strict mode. |
| Fuzzing | Two families of 2,000 generated programs each, from new random seeds every release, run before and after refactoring. |
| Real projects | 141 published open-source projects, from Click and Rich to Sphinx: Towel refactors each, and its own test suite must give the same results afterwards. |
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
