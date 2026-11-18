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
