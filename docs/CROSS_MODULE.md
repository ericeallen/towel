# Sharing helpers across modules

[Documentation index](README.md)

Cross-module sharing adds imports between project modules. It is opt-in: pass `--cross-module` on the command line or `cross_module_helpers=True` to the engine.

On this page:

- [How a cross-module import is spelled](#how-a-cross-module-import-is-spelled)
- [Resolving import refusals](#resolving-import-refusals)
- [Dependency metadata](#dependency-metadata)
- [What the preflight reports](#what-the-preflight-reports)
- [Missing and optional modules](#missing-and-optional-modules)
- [Annotation-only imports](#annotation-only-imports)

## How a cross-module import is spelled

With `cross_module_helpers` (`--cross-module`), a helper shared by several
modules lives in one of them and the others import it. Towel reads the
imports every Python file of the project already makes, and writes only an
import those show to work wherever the program runs
([Design decisions](DECISIONS.md), *Import names come from the program*):

- between two modules of one package, the relative import, or the absolute
  one when the importing module already spells its own package absolutely
  and never relatively;
- across top-level packages, an absolute import only when the importing
  package already imports the other one, spelled as it already does;
- into a directory only where the importing side already imports from it,
  with an import that runs whenever its module is imported, not one inside a
  function, so a library never borrows from the test package inside it;
- never from a module the build configuration leaves out of what ships into
  one it keeps, read in each distribution's own configuration:
  - hatch's `exclude`, `include`, `only-include`, `packages`,
    `only-packages` and its default wheel selection;
  - setuptools' `packages.find` (`where`, `include`, `exclude`), an explicit
    `packages` list, `package-dir` and `py-modules`;
  - MANIFEST.in's `exclude`, `recursive-exclude`, `global-exclude` and
    `prune` (a wheel built from the sdist lacks what the sdist does);
  - Poetry's `packages` (`include`, `from`, `format`), `include`, `exclude`
    and its default package;
  - PDM's `includes`, `excludes`, `source-includes`, `package-dir` and its
    default;
  - uv's `module-name`, `module-root`, `source-include`, `source-exclude`
    and `wheel-exclude`;
  - flit's module and sdist `exclude`;
  - scikit-build-core's `wheel.packages` and excludes;
  - what a `.gitignore` covers.

  The module left out may still borrow from the one that ships. What a
  setup.py or a build hook leaves out is not known, and a setting Towel
  cannot interpret puts the host in doubt;
- never from a module some supported platform or Python cannot import: one
  whose module-level code imports what CPython's documentation marks as
  available on some platforms only (`msvcrt`, `fcntl`, `os.startfile`,
  `signal.SIGALRM`), a module a supported Python removed, or a dependency a
  marker limits. A `try` that catches `ImportError` keeps its imports
  optional;
- never from a module the program imports only under a condition (an `if`,
  a `try`, a function body), for a borrower that does not already load it
  (`conditionally_imported_host`);
- never across distributions: a borrower in a distribution, the nearest
  directory with a `setup.py`, a `setup.cfg` with `[metadata]` or
  `[options]`, or a `pyproject.toml` with `[project]`, `[build-system]` or
  `[tool.poetry]`, borrows only from a host in the same one, since it may be
  installed against the other's released version (`other_distribution`). A
  module in no distribution, such as a root test or script, is exempt.

## Resolving import refusals

A host that no participating module can import that way is not taken, and a
pair with no such host is declined (`unproven_import`). The costs: sibling
packages that never import each other share nothing; a directory of scripts
that import nothing local gets no cross-file helpers; and a name in doubt
gets none. Each kind of doubt has its own remedy:

- a second copy in the tree, such as a stale `build/lib/alpha` beside
  `src/alpha`, or an import that names a module two ways or climbs out of
  its package: leave out the directory holding it with `--exclude`, or fix
  the import;
- a symbolic or hard link that gives a file a second name the program uses,
  such as `beta -> src/alpha` beside `alpha`, a file link `tool.py`, or a
  link inside a package that an import goes through: replace the link with a
  copy of what it names, or import that file by one name only;
- a copy the interpreter running Towel can import from outside the project,
  including one installed in the project's own `.venv`: `--exclude` cannot
  reach it, so run Towel from an environment where that name is this tree,
  such as one with the project installed editable (`pip install -e .`), or
  from one without it; if the project's directory of that name is a
  namesake, leaving it out with `--exclude` also resolves it;
- a directory named like a distribution the project requires, such as a
  `click/` beside `dependencies = ["click>=8"]`, which is what the installed
  project imports as `click`: rename the directory or leave it out with
  `--exclude`, or drop the requirement if the directory is what the program
  means;
- a directory lacking a module the program imports under its name, from
  outside it, such as a `third_party/click/` holding `utils.py` when the
  program also imports `click.core`: if the program means the library,
  rename the directory or leave it out with `--exclude`; if it means the
  directory, fix that import or leave out the directory holding it. The
  directory's own import of a module it lacks (a generated `_version.py`)
  is no such sign, and nor is any when the project's metadata names the
  project as the name's distribution, as sphinx's names `Sphinx`.

## Dependency metadata

The requirements read are PEP 621's dependencies and extras, PEP 735's
dependency groups, Poetry's dependency tables and groups, the development
dependencies of `[tool.uv]` and `[tool.pdm]`, every hatch environment's
`dependencies` and `extra-dependencies` (pyproject.toml or hatch.toml),
setup.cfg's `install_requires` and `extras_require`, a Pipfile, the
lockfiles uv, Poetry, PDM and Pipenv write, and the requirements files at
the project root: `requirements*.txt`, `*-requirements.txt` and
`*_requirements.txt`, the same with pip-tools' `.in`, and every `.txt` or
`.in` in `requirements/`, with the files they include. tox.ini, a noxfile
and CI recipes are not read. A distribution is
matched to a name by its own normalized name, so one whose import name
differs (`PyYAML` provides `yaml`) is recognized only where the interpreter
running Towel can import it: run Towel in the project's own environment.

## What the preflight reports

On the command line, a `--cross-module` run names each such problem before
it starts, with the remedy for its kind, and refuses when one leaves a name
of the package it refactors in doubt: an ambiguous name, a file reachable
under two names, a relative import that climbs out of its package, or a
top-level name found only inside a package the program also imports as one,
as `pkg/c.py`'s `import helpers` finds only `pkg/helpers.py`. The report
names the import that treats it as top-level. Only an import that attests
locates a name, makes it ambiguous, or finds it inside a package: one
inside `try`/`except ImportError`, under `TYPE_CHECKING`, or in a file
that changes `sys.path` (a setup.py that appends its package to `sys.path`
to read its own version) does none of these. One that runs can still load a
file under a second name, so it still counts for that.

## Missing and optional modules

An import of a module the tree lacks refuses nothing, wherever it lies,
however it is spelled: `from .gone import x`, `from . import gone` or
`from pkg import gone`, where `pkg`'s initializer binds no `gone`. One
under `if TYPE_CHECKING:` never runs, so it is no such import. The
run leaves the file making it exactly as it was, neither hosting nor
borrowing a helper and getting none of its own, and says so, naming the
file: test data such as sphinx's `need_mocks.py`, which imports a module its
tests mock, or an example importing a module that no longer exists, can be
left out with `--exclude`; a package's own import of the `_version.py` its
build generates, missing from a fresh clone, is resolved by installing the
project (`pip install -e .`). When that file is a package's `__init__.py`,
which importing any module below it runs, those modules host a helper only
for modules imported through the package, and the run says so in one line.

## Annotation-only imports

Without `cross_module_helpers` no import between the project's modules that
runs is ever written. A helper's annotation may still need a type from
another module; that import is written under `if TYPE_CHECKING:`, where it
never runs, spelled by the same rules, and where no import is known to work
the annotation keeps the checker's full name.
