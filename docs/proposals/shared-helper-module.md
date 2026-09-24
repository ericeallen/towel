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
- a module that runs code when imported.

The proposal, behind a flag of its own, is to put such a helper in a *new*
module at the most specific regular package containing every borrower, and
to have each borrower import it.

The new module would be a leaf. Every module-level name the moved code reads
is already passed in from each call site, so the new module imports nothing
of the project at run time. Importing it runs only `def` statements. Three
of the failure classes behind most of the earlier cross-module P1s
therefore cannot arise by construction:

- import-time effects;
- import cycles through the new module;
- new dependencies between the borrowers.

The package holding it is already imported by then, as a parent of every
borrower.

## What it must guarantee

1. **The lowest common ancestor is a regular package holding every
   borrower.** Each borrower then reaches the new module by a relative import
   that is valid wherever the package is installed. Where the common ancestor
   is a namespace directory, `src/`, or the project root, the new module
   would be a new top-level module, which ships only if packaging metadata
   changes. Such a pair stays declined.
2. **The new module ships wherever its package ships.** Every build
   backend's directory-based discovery guarantees that, but explicit file
   lists or exclude patterns can defeat it. The flag should verify by
   building the wheel and checking the file is in it.
3. **Test code does not move into the library.** When every borrower is a
   test module, the common ancestor is often the library package itself.
   Towel must not add a shipped module that holds only test helpers.
4. **Its own cycle and effect reasoning.** Towel's current checks, asked
   about the new module as a host, refuse most of the library cases, for two
   reasons: the cycle search starts at the common ancestor's `__init__.py`,
   which imports the borrowers; and the effects check counts modules the
   shared package initializers load conditionally. A leaf module that imports
   nothing cannot close a cycle or run new code, beyond the initializers every
   borrower already runs. The flag needs that argument made precise, not the
   general checks.

## Measured opportunity

The measurement covered 12 projects at their corpus commits, with the whole
repository in scope: networkx, sphinx, beautifulsoup4, tornado, rich,
pygments, click, packaging, pytest, attrs, trio and prompt-toolkit. It counts
duplicates Towel declined at host choice for an import or host reason.

| | Distinct duplicates |
|---|---|
| Declined at host choice | 63,132 |
| Common ancestor not a regular package (namespace directory, project root) | 43,214 (68%) |
| Recoverable under rule 1 | 19,914 |
| — library to library | 3,831 (316 groups) |
| — library to test | 1 |
| — test to test | 16,082 |
| Would put a shipped module holding only test helpers into the library | 949 (networkx 937, trio 12) |
| Would land in a test package that ships with the library | 304 |

Built wheels confirm which files ship. networkx, trio and tornado ship their
in-package tests; beautifulsoup4 does not ship `bs4/tests`.

**Most library recoveries are small.** By statement count, 743 have one, 228
two, 2,457 three and 403 four or more. 956 span at least five lines and 45
at least ten.

**The largest are the kind of duplication worth removing.** Sphinx's C and
C++ domain parsers share `_parse_string` (16 statements),
`_parse_cast_expression` (15), `ASTDeclSpecs._stringify` (14, 37 regions in
7 files) and `Symbol._add_symbols` (13). networkx's flow algorithms share
`augment` (16), and its graph classes share `add_edge` (13, 13 regions in
4 files). All ten largest are declined today as import cycles, and all ten
pass once the initializers shared by the new module and every borrower are
exempted from the cycle and effect checks.

**What the same exemption would accept, in a prototype of the study's own
construction (not Towel's):** 3,772 of the 3,831 library-to-library
duplicates, and 15,473 test-to-test duplicates before rule 3 removes the
ones that would move test code into the library. Towel's checks as written
accept 684 and 15,345. Every figure is an upper bound: the checks that run
after host choice (differing bindings, builtins, narrowing, overlap) were not
run against a new module. Only 24 of the 19,914 helpers read a module-level
name bare.

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
