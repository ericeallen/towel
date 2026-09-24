# A shared helper module for duplicates across unrelated directories

Status: proposal, 2026-09-24, from the owner. Measured, not scheduled; not
part of 1.772.

## The idea

With `--cross-module`, a helper lives in one of the modules whose code it
replaces, and the others import it. Towel refuses when that import would be
unsound, or when it cannot show that it is sound:

- a new dependency between packages that did not import each other;
- a directory its own side never imports from;
- an import cycle;
- an import that would load a module with import-time effects that the
  borrower did not already load.

The proposal, behind a flag of its own, is to put such a helper in a *new*
module at the most specific regular package containing every borrower, and
to have each borrower import it.

The new module would be a leaf. Its module level would import nothing of
the project, so importing it would run nothing but its own definitions.
The package holding it is already imported by then, as a parent of every
borrower. Three reasons for refusing then cannot arise:

- import-time effects;
- import cycles through the new module;
- new dependencies between the borrowers.

The fourth, the directory rule, gives way to rule 1 below: the package
structure itself fixes the name each borrower imports the new module by.

This needs two things of the helpers:

- **Module-level names come in as parameters.** Towel already renders
  cross-module helpers that way: networkx's `augment` helper takes `nx`, and
  sphinx's `_parse_cast_expression` helper takes `ASTCastExpr` and
  `DefinitionError`. In the measurement, 24 of the 19,914 recoverable
  duplicates have a helper that reads a module-level name directly, and it
  would have to be passed in too.
- **Imports inside the moved code stay inside it.** A duplicate that itself
  contains an import, such as prompt-toolkit's
  `from prompt_toolkit.layout.controls import BufferControl` inside a
  function, keeps it in the helper body. It runs when the helper is called,
  as it did before. A relative import there would have to be respelled for
  the new module's package, or the duplicate declined.

## What it must guarantee

1. **The lowest common ancestor is a regular package, and so is every
   directory between it and each borrower.** Each borrower then reaches the
   new module by a relative import that is valid wherever the package is
   installed. The duplicate stays declined where the common ancestor is not
   a regular package: a namespace directory, `src/` or the project root.
   There the program's own imports fix no name for the new module, and it
   would ship only if packaging metadata changed.
2. **The new module ships wherever its package ships.** Every build
   backend's directory-based discovery guarantees that, but explicit file
   lists or exclude patterns can defeat it. The flag should verify by
   building the wheel and checking the file is in it.
3. **Test code does not move into the library.** When every borrower is a
   test module, the common ancestor is often the library package itself.
   Towel must not add a shipped module that holds only test helpers.
4. **Its own cycle and import-change reasoning.** Asked about the new
   module as a host, Towel's current checks refuse most of the library
   cases, for two reasons:
   - The cycle search starts at the common ancestor's `__init__.py`, which
     imports the borrowers.
   - The import-change check treats as new the modules that the package
     initializers load conditionally. Those initializers are shared with
     every borrower, and their conditional imports are measured against
     what each borrower loads unconditionally.

   Every initializer above the new module has already run, or is running,
   by the time any borrower imports it. It is still running when the
   borrower is itself the common package's `__init__.py`. Either way the
   package is already in `sys.modules`, and a leaf module whose module level
   imports nothing cannot close a cycle or load anything new at import time.
   The flag needs that argument made precise, not the general checks.

## Measured opportunity

The measurement covered 12 projects at their manifest commits: networkx,
sphinx, beautifulsoup4, tornado, rich, pygments, click, packaging, pytest,
attrs, trio and prompt-toolkit. It used Towel at 64c0b18 with
`--cross-module`; seven configurations re-run at a9e15e7 made identical
decisions. The whole repository was in scope except two directories:
sphinx's test fixtures (`tests/roots`) and prompt-toolkit's `examples/`.
Each holds a broken import, sphinx's deliberately as a test fixture, and
Towel then refuses the entire run. Narrowing that refusal is being fixed for
1.772. The count is of duplicates Towel declined at host choice for an
import or host reason.

| | Distinct duplicates |
|---|---|
| Declined at host choice | 63,132 |
| Common ancestor not a regular package (namespace directory, project root) | 43,214 (68%) |
| Common ancestor a regular package | 19,918 |
| — excluded: a borrower is written to run by path, where a relative import fails | 4 |
| Recoverable | 19,914 |
| — library to library | 3,831 (316 groups) |
| — library to test | 1 |
| — test to test | 16,082 |
| Would put a shipped module holding only test helpers into the library | 949 (networkx 937, trio 12) |
| Would land in a test package that ships with the library | 304 |

Four more of the test-to-test duplicates counted as recoverable have a
borrower below a directory that is not a package, inside the common
ancestor. Rule 1 as stated declines them too.

The study built all twelve wheels, which settle what ships. networkx, trio
and tornado ship their in-package tests; beautifulsoup4 does not ship
`bs4/tests`.

**Most library recoveries are small.** By statement count, 743 have one, 228
two, 2,457 three and 403 four or more. 956 span at least five lines and 45
at least ten.

**The largest are the kind of duplication worth removing.** Sphinx's C and
C++ domains share:

- the parser methods `_parse_string` (16 statements) and
  `_parse_cast_expression` (15);
- `ASTDeclSpecs._stringify` (14 statements, in a group of 37 regions across
  7 files);
- `Symbol._add_symbols` (13).

In networkx, the flow algorithms share `augment` (16 statements), and the
graph classes share `add_edge` (13, in a group of 13 regions across 4
files). Towel declines all ten of the largest as import cycles. All ten pass
the cycle and import-change checks once the initializers shared by the new
module and every borrower are exempted.

**What the exemption would accept.** The exemption is a prototype the study
built, not Towel's code. With it, the checks accept:

- 3,772 of the 3,831 library-to-library duplicates;
- 15,473 test-to-test duplicates, or 14,526 once rule 3 removes the 947 that
  would move test code into the library.

Towel's checks as written accept 684 and 15,345. The 59 library cases still
refused are all cycles, from two causes:

- **57 come from an import inside the moved code.** The probe put every
  helper for one package into a single module, and one helper in each such
  module carries an import of its own, such as prompt-toolkit's
  `BufferControl`. The cycle search follows that import, although it runs
  only at call time.
- **2 have the common package's own `__init__.py` as a borrower.** Examples
  are `sphinx/directives/__init__.py` and `sphinx/transforms/__init__.py`.
  The prototype's exemption does not cover that initializer; rule 4's
  argument does.

Every figure is an upper bound. The checks that run after host choice
(differing bindings, builtins, narrowing, overlap) were not run against a
new module.

## Open questions

- **Naming.** A private name (`_towel_shared.py`, or one per package) that
  the paired agent is expected to rename or split, as it renames helpers.
- **Growth.** Later runs should append to one shared module per package
  rather than create a new one each time.
- **Moving the helper later.** When a shared module's only callers are later
  moved or deleted, the paired agent decides where its helpers belong, as it
  does for helpers shared across classes.
- **Cost of verifying shipping.** Building the wheel costs seconds to minutes
  per run and needs the build backend. An explicit statement of intent from
  the user may be enough to skip it.

The study's scripts, per-pair records and wheels are the evidence for these
figures, and should accompany an implementation.
