# Glossary

```python
return __extracted_func_0(amount, 0.10)  # a call to a helper, with one argument
```

The words Towel's pages and messages use, each in plain terms. Terms are grouped
by when you meet them: running Towel, reading its output, and the rarer cases.

## Running Towel

Block
:   A run of consecutive statements inside a function or method, or, from 2.0,
    an expression spanning several lines. Towel compares blocks with one
    another; `--min-lines` sets the shortest it considers (default 3).

Duplicate pair
:   Two blocks with the same shape, differing only in names, values or
    expressions. Towel considers duplicates two at a time and either extracts
    them into a helper or leaves them alone.

Preview
:   `towel preview` shows what a run would change, as a summary and a diff,
    without writing anything.

Dry
:   `towel dry` performs the refactoring and writes it, into a new output
    directory or, if you name the input twice, in place.

Fixed point
:   One extraction can expose another, so Towel repeats the search on its own
    output until a pass finds nothing more to extract. That final state is the
    fixed point. `--max-refactorings N` stops after N extractions instead.

Left alone, or declined
:   Towel found duplicate blocks but did not extract them, because it could not
    show that the change keeps the program's behavior or because the helper
    would cost more to read than the code it replaces. A run that extracts
    nothing gives the reasons. With `--explain`, each reason lists up to 20
    distinct places from the final analysis ([reasons](WHY_LEFT_ALONE.md)).

Refused
:   A run, or a request such as a rename, that stops before writing anything,
    with a message that says why and what to change.

Relaxed mode
:   The default from Towel 2.0. Towel preserves what the program computes and
    does: its results, its output, its exceptions and the order of its side
    effects. It assumes three things nearly every program satisfies: that it
    does not depend on when an object it no longer uses is released, that
    reading an attribute, indexing, comparing and arithmetic have no side
    effects, and that module names are not rebound while a helper runs. A few
    narrower conditions are on [relaxed and strict](RELAXED_AND_STRICT.md).

Strict mode
:   `--strict` makes Towel also preserve exactly when objects are released and
    when names are looked up, as releases before 2.0 did. Expect more blocks
    left alone, and extra bookkeeping in the callers of those it extracts.

Typed run
:   Towel checks each change with the project's type checker (mypy or Pyright,
    as the project configures, otherwise whichever is installed) and keeps only
    changes that add no new errors. `--no-types` turns this off.

Baseline
:   The type errors a project already has before Towel changes it. A typed run
    rejects a change only for errors the baseline cannot account for.

## Reading the output

Helper
:   The function Towel writes to hold code that two or more blocks shared. Each
    original block becomes a call to it. Towel names helpers
    `__extracted_func_0`, `__extracted_func_1` and so on until you
    [rename them](NAMING.md).

Parameter
:   Each difference between the duplicated blocks becomes a parameter of the
    helper, named `_param_0` or `__param_0` and so on until you rename it.
    `--max-parameters` limits how many a helper may take (default 5).

Anti-unification
:   The method Towel uses to build a helper: the structure the blocks share
    becomes the helper's body, and the places where they differ become its
    parameters.

Thunk
:   A zero-argument lambda, such as `lambda: welcome_email(user)`, passed to a
    helper in place of a value. The helper calls it only where the original code
    evaluated that expression, so it runs as often, and as late, as it did
    before. [Reading Towel's output](GENERATED_CODE.md#a-lambda-that-delays-an-argument)
    shows an example.

Callback
:   A lambda with a parameter, such as `lambda item: item.weight_kg`, passed
    where the copies differ in something computed from a variable of the
    helper's own loop or block.

Reuse, or redirect
:   When one copy is already the whole body of a function, the other copies
    call that function instead of a new helper; the run says "Reuse NAME".

`# towel: no-extract`
:   A comment placed after a function's final colon that keeps that function out
    of every extraction. The [command reference](CLI_GUIDE.md#protect-a-function)
    shows where it goes.

## Naming helpers

Helper inventory
:   The list of every generated helper and parameter in a project, printed by
    `towel rename-helpers PATH --list --json`.

Rename key
:   The name of one helper or parameter in the inventory, such as
    `charges.py:__extracted_func_0` or `charges.py:__extracted_func_0.__param_0`.
    A JSON file that maps rename keys to new names renames them all at once.

Consumers
:   The modules that call or import a helper. A rename must change every one of
    them, so Towel needs to see the whole project; `--complete-scope` tells it
    that the directory you gave contains every caller.

## Sharing across modules

Cross-module extraction
:   A helper shared by blocks in two different modules: one module holds it and
    the other imports it. It is off unless you pass `--cross-module`, because it
    adds imports between your modules.

Host
:   In a cross-module extraction, the module that holds the helper.

Borrower
:   In a cross-module extraction, a module that imports the helper from its
    host.

## Rarer terms

Ownership holder
:   In strict mode, when a whole function body becomes a helper, the caller may
    pass its arguments through an extra last parameter named `_towel_owner`, so
    that objects are released in the same order as before. Relaxed mode does not
    add it.

Recovery journal
:   When Towel changes files in place, it keeps their original contents in a
    `.towel-transaction-<id>` directory until the write is complete. If the
    write is interrupted, `towel recover` on that directory puts the originals
    back; see the [command reference](CLI_GUIDE.md#recovery-and-interrupted-writes).

[Documentation index](README.md)
