# Towel

**Towel finds duplicated Python code and refactors it for you.** It moves each
repeated block into a shared helper function and replaces the copies with
calls, even when the copies differ in a few names or values. Linters such as
pylint's `duplicate-code` tell you where the copies are; Towel writes the
helper and the calls, and leaves alone anything it cannot change safely.

[![PyPI](https://img.shields.io/pypi/v/code-towel?label=code-towel)](https://pypi.org/project/code-towel/)
[![Python](https://img.shields.io/pypi/pyversions/code-towel)](https://pypi.org/project/code-towel/)
[![CI](https://github.com/ericeallen/towel/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ericeallen/towel/actions/workflows/ci.yml)

Two functions in `src/accounts.py` check a form the same way, except for the
variable they check and the shortest name they allow. Ask Towel what it would
do; `preview` changes nothing:

```bash
towel preview src/
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (2 functions).
Would change 1 file(s):
  accounts.py: 1 refactoring(s)
    - Extract common code from register_user and register_admin
--- before/accounts.py
+++ after/accounts.py
@@ -1,16 +1,16 @@
+def __extracted_func_0(__param_0, __param_1):
+    if not __param_0.get("name"):
+        raise ValueError("name is required")
+    if len(__param_0["name"]) < __param_1:
+        raise ValueError("name is too short")
+    return save(__param_0)
+
+
 def register_user(form):
     user = dict(form)
-    if not user.get("name"):
-        raise ValueError("name is required")
-    if len(user["name"]) < 2:
-        raise ValueError("name is too short")
-    return save(user)
+    return __extracted_func_0(user, 2)


 def register_admin(form):
     admin = dict(form, role="admin")
-    if not admin.get("name"):
-        raise ValueError("name is required")
-    if len(admin["name"]) < 8:
-        raise ValueError("name is too short")
-    return save(admin)
+    return __extracted_func_0(admin, 8)
Input files were not changed.
```

What differed between the copies became the helper's arguments. `towel dry`
then makes the change: despite its name, it writes, and asks first (the name
is for "don't repeat yourself"). `towel rename-helpers` gives the helper and
its parameters real names; then run your tests and keep the change. The
[quick start](https://github.com/ericeallen/towel/blob/v1.949/docs/QUICKSTART.md)
does all of that in ten minutes.

## Get started

```bash
uv tool install code-towel     # or: pipx install code-towel
towel preview src/             # show what would change; writes nothing
towel dry src/ src/            # make the change in place: commit your work first
towel rename-helpers src/ --list   # the helpers to name, and their parameters
```

Giving the input path again as the output, as here, refactors the project in
place. To keep the original, give `dry` a new directory instead:
`towel dry src/ cleaned/`. A separate output path must not already exist or
overlap the input.

## What Towel will not do

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

[Relaxed and strict](https://github.com/ericeallen/towel/blob/v1.949/docs/RELAXED_AND_STRICT.md)
explains each assumption and when to use `--strict`.

## Install

**Release status: 1.949 (beta).** This README and the guides it links
describe Towel 2.0; `towel --version` shows the version you have. Versions
before 2.0 have no relaxed mode (they behave as `--strict` does), no
`--check` and no `[tool.towel]`. The
[GitHub releases page](https://github.com/ericeallen/towel/releases) lists every
published version with its release notes.

The PyPI package is **[`code-towel`](https://pypi.org/project/code-towel/)**;
the command is **`towel`**. The PyPI name `towel` belongs to an unrelated project,
so `pip install towel` and `uvx towel` will not install this tool.

Towel runs on Python 3.11 to 3.15. Install it as a command-line tool in an
environment of its own, with [uv](https://docs.astral.sh/uv/) or
[pipx](https://pipx.pypa.io/):

```bash
uv tool install code-towel     # or: pipx install code-towel
towel --version
towel --help
```

uv picks the newest Python it can, now 3.14 or 3.15, and downloads one when
the machine has none. On Python 3.14 and later, `--strict` leaves alone what
depends on the order Python frees objects, which CPython 3.14 changed, so it
extracts less; the default, relaxed mode, is the same on every version. If you
will use `--strict`, install on 3.13 with
`uv tool install --python 3.13 code-towel`. Without uv or pipx,
make a virtual environment with a Python in that range and install into it:

```bash
python3 --version              # must be in that range
python3 -m venv .venv && .venv/bin/pip install "code-towel>=1.414"
.venv/bin/towel --version
```

A bare `pip install code-towel` often fails on a system Python: Homebrew's and
Debian's Pythons refuse to install outside an environment. The `>=1.414` keeps
an old pip, such as the one with macOS's Python 3.9, from installing a
withdrawn 1.0 release instead.

Towel itself needs only the standard library. To have it format the code it
inserts and type-check the helpers it writes, install the optional tools into
the same environment:

```bash
uv tool install "code-towel[format,types]"     # or: pipx install "code-towel[format,types]"
.venv/bin/pip install "code-towel[format,types]>=1.414"
```

A type checker needs your project's dependencies, which an environment of
Towel's own does not have; for a typed project, run Towel in the project's
environment
([typed projects](https://github.com/ericeallen/towel/blob/v1.949/docs/TYPING.md)).

`--no-format` and `--no-types` turn either off for one run. Upgrade with
`uv tool upgrade code-towel`, `pipx upgrade code-towel` or
`.venv/bin/pip install --upgrade code-towel`, or try Towel without installing
it: `uvx --from code-towel towel --help`.

## Learn more

- [Quick start](https://github.com/ericeallen/towel/blob/v1.949/docs/QUICKSTART.md): a first refactoring, named, tested and kept.
- [Reading Towel's output](https://github.com/ericeallen/towel/blob/v1.949/docs/GENERATED_CODE.md): helpers, parameters and the occasional `lambda`.
- [Why something was left alone](https://github.com/ericeallen/towel/blob/v1.949/docs/WHY_LEFT_ALONE.md): every reason Towel gives, and what to do.
- [Using Towel on a project](https://github.com/ericeallen/towel/blob/v1.949/docs/USING_TOWEL.md): branches, scope, long runs and reviewing a large change.
- [Recipes](https://github.com/ericeallen/towel/blob/v1.949/docs/RECIPES.md): CI, pre-commit and `[tool.towel]` settings.
- [Naming helpers](https://github.com/ericeallen/towel/blob/v1.949/docs/NAMING.md) and the [command reference](https://github.com/ericeallen/towel/blob/v1.949/docs/CLI_GUIDE.md).
- [All documentation](https://github.com/ericeallen/towel/blob/v1.949/docs/README.md), also on [the documentation site](https://ericeallen.github.io/towel/).

## Requirements

Python 3.11 to 3.15 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis forks worker processes, as macOS
and Linux both allow; where Python cannot fork, analysis runs on a single core.
Windows is not a supported or validated release platform.

One core is enough, and `TOWEL_WORKERS=1` limits analysis to one worker. A
module takes seconds; a large project with type checking can take an hour.
[Requirements and performance](https://github.com/ericeallen/towel/blob/v1.949/docs/REQUIREMENTS.md)
has the figures.

## Contributing and security

Contributors can start with
[development setup and checks](https://github.com/ericeallen/towel/blob/v1.949/CONTRIBUTING.md).
Report security issues through the
[security policy](https://github.com/ericeallen/towel/blob/v1.949/SECURITY.md).

## License

Towel is licensed under the [Apache License 2.0](https://github.com/ericeallen/towel/blob/v1.949/LICENSE);
its source files carry Eric Allen copyright headers.

## Releases

The [changelog](https://github.com/ericeallen/towel/blob/v1.949/CHANGELOG.md)
describes the user-visible changes in this version and in each earlier one.
The [GitHub releases page](https://github.com/ericeallen/towel/releases) lists
the published versions with their release notes and artifacts, and
[PyPI](https://pypi.org/project/code-towel/#history) lists the installable
packages.
