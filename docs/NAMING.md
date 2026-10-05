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
- [Checks and other options](#checks-and-other-options)

## Review and adopt the extraction

First extract into a copy and inspect the diff:

```bash
towel dry path/to/project path/to/cleaned --no-interactive
diff -ru path/to/project path/to/cleaned
```

Before renaming, adopt the reviewed output into the project using your normal
version-control workflow. In particular, a copied package directory may have
the output directory's name instead of its import name. The rename checker
needs the package in its real project context to find and update importers.
It refuses a batch when it cannot match those imports.

The commands below assume `path/to/project` now contains the adopted result.

## Export, propose and apply names

```bash
# Export the helper and parameter inventory.
towel rename-helpers path/to/project --list --json > helpers.json

# Read helpers.json and prepare renames.json yourself or with a coding assistant.
# Each key must be an inventory key; its value is the proposed new name.

# Preview the batch, apply it, then run the project's tests.
towel rename-helpers path/to/project --rename-file renames.json --preview
towel rename-helpers path/to/project --rename-file renames.json
```

Review the proposed names before applying. The shared `towel-rename` skill in
the agent-skills repository guides an assistant through extraction, review,
naming and tests. Towel's interactive naming mode also works without an
assistant and calls no LLM service.

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
