# Naming generated helpers

[Documentation index](README.md)

Towel generates placeholders such as `__extracted_func_3` and `__param_0`.
Extraction is mechanical; choosing a useful name requires understanding the
code. Naming is therefore a separate step, reviewed by you and optionally
assisted by an LLM.

On this page:

- [Review and adopt the extraction](#review-and-adopt-the-extraction)
- [Export, propose and apply names](#export-propose-and-apply-names)
- [What the inventory contains](#what-the-inventory-contains)
- [Rename keys and class-private names](#rename-keys-and-class-private-names)
- [Definition selection and consumer scope](#definition-selection-and-consumer-scope)
- [Checks and other options](#checks-and-other-options)

## Review and adopt the extraction

First extract into a copy and inspect the diff:

```bash
towel dry path/to/project path/to/cleaned --no-interactive
diff -ru path/to/project path/to/cleaned
```

Rename in a **complete project context**. If the output copies the whole
project and preserves its internal module names, you can rename and run its
tests there before adopting any changes into the original. Use an environment
whose imports resolve to that copy: an editable installation that still points
at the original project can make the copied package ambiguous. Install the copy
editable in its own environment when the project needs installation. The
[runnable quick start](QUICKSTART.md) demonstrates this with exact commands.

If you copied only a package directory, its new directory name or missing
surrounding modules can change what imports mean. Put the reviewed files into
a separate development checkout at their original relative paths before
renaming. The checker refuses when it cannot resolve the package's importers.
Do not use `--complete-scope` to claim that known outside consumers do not exist.

The commands below assume `path/to/cleaned` is the complete copied project.

## Export, propose and apply names

```bash
towel rename-helpers path/to/cleaned --list --json > helpers.json
```

Read each helper and its call sites. Copy the exact `rename_key` from the
inventory into a JSON object whose values are your chosen names. For example,
if the inventory reports these keys, `renames.json` can contain:

```json
{
  "charges.py:__extracted_func_0": "discounted_total_with_shipping",
  "charges.py:__extracted_func_0.__param_0": "amount"
}
```

Only include keys present in your inventory. The parameter entry is optional;
a helper whose input is already named `amount` does not have that `__param_0`
key. A single helper-name entry is a complete valid batch.

```bash
towel rename-helpers path/to/cleaned --complete-scope --rename-file renames.json --preview
towel rename-helpers path/to/cleaned --complete-scope --rename-file renames.json
# Run the copied project's tests before adopting its changed files.
```

You can prepare this mapping entirely by hand. A coding assistant can also
suggest names after reading the inventory; review those suggestions before
applying them. Towel's interactive mode prints a prompt and accepts pasted
JSON, and calls no LLM service. The shared `towel-rename` skill in the
agent-skills repository guides an assistant through the same review workflow.

## What the inventory contains

The JSON inventory includes each helper's scope, source and call sites. For
each parameter it records the evaluation kind (`value`, `thunk`, `lifted` or
`receiver`) and the actual arguments supplied at every site. Those arguments
help distinguish, for example, a parameter representing an email address from
one representing its containing account.

Each helper's `changes` list pairs the original block (`before`) with its
replacement call (`after`), including sites that were a function's whole body.
This context helps a reader choose a name, write a docstring or assess types.

The before/after information comes from `.towel-helpers.json`, which `dry`
writes beside its output. It is optional metadata for the naming step and can
be deleted when that step is complete. If it cannot be written, `dry` reports
a warning and exits successfully because source publication has succeeded.
Helper inventory and renaming still work; call-site before/after information
is unavailable. Existing sidecar collisions and symlink targets are preserved.

## Rename keys and class-private names

Use the exact mapping key supplied by the inventory:

| Key shape | Scope |
| --- | --- |
| `path.py:helper` | Module-level helper and its importers. |
| `path.py:Class.__helper` | Class-private method and references in that class body. |
| `path.py:helper.__param_0` | Parameter within a module-level helper. |
| `path.py:Class.__helper.__param_0` | Parameter within a class-private method helper. |

Class-private helpers must remain class-private: a new name starts with two
underscores and does not end with two. Otherwise the batch is refused. This
protects against accidental overrides by ordinary differently named
subclasses. See [method insertion](KNOWN_LIMITATIONS.md#method-insertion) for
the exact scope of that protection.

An older class-level helper without leading double underscores is still
renamed through its bare key, `helper`, together with its attribute references.

Keys identify Python's normalized bindings. For example, a definition spelled
`__extracted_func_𝟘` has the inventory name `__extracted_func_0`; use the
inventory key to rename its definition and resolved references consistently.
A destination must already be NFKC-normalized: use `K`, not `K`. Collision
checks use normalized names and Python's class-private stored names. Edits
use the original source-token spans and preserve untouched source bytes,
including encoding markers and newline conventions.

A private spelling inside a nested class does not necessarily reference an
outer parameter of that spelling: Python first applies that class's mangling.
The renamer resolves that stored binding before editing, and refuses a batch
if the new name would capture a previously distinct class-private reference.

## Definition selection and consumer scope

Use the project root as the target, and path-qualified keys or `--file` to
select definitions. Selection does not narrow consumer analysis: ordinary
imports, re-exports, qualified references and supported keyword callers are
resolved across the enclosing project. A narrow target refuses before any
write if an outside consumer needs an edit. The diagnostic names that file
and the root at which to rerun. Stable user aliases can keep an outside
consumer unchanged when the selected package preserves that alias.

Project discovery uses the nearest packaging marker (`pyproject.toml`,
`setup.cfg`, or a non-package `setup.py`) or regular-package ancestry. A
namespace directory alone cannot establish its surrounding project. For an
unmarked project, select its complete source root and explicitly declare the
scope. The main workflow above includes this declaration; it can be omitted
when project discovery already establishes the complete root:

```bash
towel rename-helpers path/to/project --complete-scope --rename-file renames.json
```

The flag declares that the target contains every ordinary consumer; it does
not override a discovered outside consumer or permit writes outside the
target. Listing needs no declaration. Library callers of `plan_renames`
retain the complete-target precondition when discovery has no anchor; for a
standalone file its containing directory is the supported consumer scope.

Discovery excludes symlinks, hidden/tool directories, marked environments and
checker probes under the shared source-discovery policy. An unreadable
included file or subtree aborts the batch. Consumers outside this supported
project scope, including installed external clients, require the caller's
review; a local scan cannot establish their completeness. Reflective lookup
remains outside the rename contract.

## Checks and other options

Renames are checked and applied as one batch. Name collisions, explicit uses
of a class-private mangled name such as `obj._Class__helper`, or dynamic
references can abort the entire batch with a reason. A rejected name proposal
changes nothing. File replacement and interrupted-write recovery have the
[normal batch-application limits](CLI_GUIDE.md#output-and-in-place-changes).

- `--preview` reports changes without writing; add `--json` for JSON output.
- `--file` and `--function` are repeatable filters on the inventory.
- `--llm claude|gpt|copilot|generic` adjusts the interactive assistant prompt.

Run your project's tests after applying the names, as after the extraction.
