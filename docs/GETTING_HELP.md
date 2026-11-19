# Getting help

A useful report fits on one screen:

```text
towel 2.0, Python 3.13.7, macOS 15
Command: towel preview src/ --explain
Printed: src/a.py:3-9 and src/b.py:4-10: <the reason Towel gave>
Expected: one helper shared by both functions
```

## Where to ask

Ask on the [issue tracker](https://github.com/ericeallen/towel/issues). It is the
only place: Towel has no chat room, mailing list or forum, and GitHub Discussions
is not enabled. Questions are welcome there as well as bug reports and feature
requests; search the open and closed issues first, since someone may have asked
already.

Towel is maintained by one person, so there is no promised response time.

## What to put in a report

A report that lets someone run what you ran gets an answer fastest. Include:

- **The versions:** the output of `towel --version`, `python --version` and your
  operating system. For a typed run, also the version of mypy or Pyright.
- **The exact command** you ran, with every option.
- **What Towel printed,** in full. For a block Towel left alone, add the output of
  the same command as `towel preview PATH --explain`, which names the reason for
  each pair.
- **The smallest code that shows it:** two functions with the duplicated block is
  usually enough. Remove anything confidential; never paste credentials.
- **What you expected,** and, if Towel changed your program's behavior, the test
  or output that shows the difference.

The bug-report template on the [new-issue page](https://github.com/ericeallen/towel/issues/new/choose)
asks for the same things.

## What counts as a bug

**What Towel promises.** A change Towel makes preserves the program's results,
output, exceptions and order of side effects. This holds for programs that do
not use reflection or self-instrumentation, including through code they call.
Examples include reading frames, source or line numbers with `inspect.stack()`,
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

## Security problems

Do not report a security problem in a public issue. Use GitHub's private
**Report a vulnerability** button on the repository's Security tab, as the
[security policy](https://github.com/ericeallen/towel/blob/main/SECURITY.md)
describes; only the maintainer can read what you send there.

## Contributing a fix

If you would like to fix something yourself, the
[contributing guide](https://github.com/ericeallen/towel/blob/main/CONTRIBUTING.md)
explains how to set up a development environment and run the checks, and the
[code of conduct](https://github.com/ericeallen/towel/blob/main/CODE_OF_CONDUCT.md)
applies to every conversation about the project.

[Documentation index](README.md)
