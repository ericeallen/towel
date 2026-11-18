# Towel documentation

Towel finds repeated Python code and moves it into a shared helper function,
replacing each copy with a call.

```bash
uv tool install code-towel     # or: pipx install code-towel
towel preview src/             # show what would change; writes nothing
```

```diff
Note: formatting generated code with Black (project configuration).
Would apply 1 refactoring(s).
...
+def __extracted_func_0(__param_0, __param_1):
+    if not __param_0.get("name"):
+        raise ValueError("name is required")
+    if len(__param_0["name"]) < __param_1:
+        raise ValueError("name is too short")
+    return save(__param_0)
...
-    if not user.get("name"):
-        raise ValueError("name is required")
-    if len(user["name"]) < 2:
-        raise ValueError("name is too short")
-    return save(user)
+    return __extracted_func_0(user, 2)
...
Input files were not changed.
```

Two functions checked a form the same way; the helper takes what differed,
the variable and the shortest name allowed, as arguments. The
[quick start](QUICKSTART.md) takes you from here to a named, tested change in
ten minutes. This output comes from an install with Black available; the plain
install prints a different first line.

These pages describe Towel 2.0; `towel --version` shows the version you have.
Versions before 2.0 have no relaxed mode (they behave as `--strict` does), no
`--check` and no `[tool.towel]`; the
[releases page](https://github.com/ericeallen/towel/releases) has each version's
notes.

## Getting started

- [Quick start](QUICKSTART.md): install Towel, and make, name, test and keep a first change.
- [Using Towel on a project](USING_TOWEL.md): branches, scope, long runs and reviewing a large change.
- [Frequently asked questions](FAQ.md).

## Guides

- [Reading Towel's output](GENERATED_CODE.md): helpers, parameters, and the occasional `lambda`.
- [Relaxed and strict](RELAXED_AND_STRICT.md): what the default assumes, and when to use `--strict`.
- [Why something was left alone](WHY_LEFT_ALONE.md): every reason Towel gives, what to do, and when to stop.
- [Typed projects](TYPING.md): mypy and Pyright, and the annotations Towel writes.
- [Sharing across modules](CROSS_MODULE.md): `--cross-module`, and what to do when it shares nothing.
- [Naming helpers](NAMING.md): give helpers and parameters real names.
- [Recipes](RECIPES.md): CI, pre-commit, `[tool.towel]`, monorepos and large projects.
- [Gallery](GALLERY.md): extractions in five real projects.

## Reference

- [Command reference](CLI_GUIDE.md): every option, file exclusions, in-place runs and recovery.
- [Automation and compatibility](COMPATIBILITY.md): exit statuses, the JSON result and what may change between releases.
- [Python API guide](USAGE_GUIDE.md): calling Towel from Python.
- [Known limitations](KNOWN_LIMITATIONS.md): what Towel cannot see, and what it leaves alone.
- [Requirements](REQUIREMENTS.md) and [performance](PERFORMANCE.md): platforms, memory and how long a run takes.
- [Glossary](GLOSSARY.md).
- [How Towel is validated](VALIDATION.md) and [how Towel compares](COMPARISON.md) with clone detectors, an IDE's extract-method and AI assistants.
- [Changelog](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md) and [getting help](GETTING_HELP.md).

## Contributors

- [Contributing](https://github.com/ericeallen/towel/blob/main/CONTRIBUTING.md): setup, coding standards, tests and documentation maintenance.
- [Security policy](https://github.com/ericeallen/towel/blob/main/SECURITY.md) and [code of conduct](https://github.com/ericeallen/towel/blob/main/CODE_OF_CONDUCT.md).
- [Design](design/README.md): architecture, design decisions, the full rules behind each guide, and proposals.
- [Maintainers' records](maintainers/README.md): the release procedure, release log, readiness reports, reviews and audits.

For an overview of the project, return to the [project README](https://github.com/ericeallen/towel/blob/main/README.md).
