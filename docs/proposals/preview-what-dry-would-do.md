# A preview that shows what `dry` would do

Status: proposal, 2026-09-25, from the owner; for a release after 1.772.

## The problem

`towel preview` and `towel dry` answer different questions, and nothing says
so. `preview` builds its engine with no type oracle and runs one analysis
pass, so it lists the untyped proposals of the first pass. `dry` runs the
default typed pipeline to a fixed point: it verifies each change with the
project's checker, formats it, and pairs again after each batch of
applied changes. On a typed project, then, a preview is only an upper bound
on what `dry` applies. Some of what it lists, typed verification declines,
and it cannot show what later passes find once earlier changes are in.

The two commands also take different flags. `preview` rejects `--types`,
`--no-types`, `--format` and `--no-interactive`, so a script cannot run the
same command line through both.

## The proposal

`preview` takes `dry`'s flags, with `dry`'s defaults, and shows exactly
what `dry` with those flags would write. It writes nothing to the project.

It is exact by construction, because it is the same code path. `dry` already
supports an out-of-place run, which builds its result in a stage and
publishes it to the output directory. A preview is that run with the output
in a private temporary directory that is deleted afterwards. What it prints
is the difference between the project and that output, together with what
`dry` itself reports. Any other design, such as one typed analysis pass
without the fixed point, would be a second pipeline that can disagree with
the first, and the point of the change is to remove that disagreement.

| Flag | `dry` today | `preview` proposed |
|---|---|---|
| `--types` / `--no-types` | on | same, on |
| `--format` / `--no-format` | on where a formatter is configured | same |
| `--cross-module`, `--parameterize-builtins`, `--exclude`, `--min-lines`, `--max-pairs`, `--max-parameters` | as documented | same, as today |
| `--no-interactive` / `--interactive` | prompts before writing | accepted; a no-op, since a preview writes nothing and never prompts |
| `--max-refactorings` | as documented | same |
| `--progress` | as documented | same |

## What a preview prints

- The unified diff of every file `dry` would change, formatted as `dry`
  would write it.
- The helper inventory that `dry` writes to `.towel-helpers.json`: each
  helper, its parameters and their bindings, the input the rename step reads.
- The run report: the refactorings applied, and the declined pairs and
  proposals counted by reason (`RunReport`). A typed preview therefore also
  shows what typed verification declined, and why, which is the second
  thing a user wants to see before running `dry`.
- The same refusals `dry` would give, with the same exit status. A preview
  of a project `dry` would refuse must refuse too, or it has shown something
  `dry` would never do.

`--json` would print the same content as one machine-readable object, for
agents; the `towel-rename` skill could then preview before it writes.

## Cost

A typed preview costs what the typed `dry` costs, because it is the same
run. That is the price of exactness. A preview has been a quick look until
now, and a typed one on a large project will not be. `--no-types` gives an
untyped preview at the untyped `dry`'s cost. Today's one-pass listing stays
available only if we keep it as its own mode (see the open questions).

Implementation cost is small. Most of it is output:
- route `preview` through `_run_dry`'s out-of-place path with a temporary
  output;
- print the diff, the inventory and the report;
- make sure that a preview can never publish into the project, and that it
  removes its temporary output on every exit path, interrupts included.

## Tests

- For a handful of battery fixtures and one typed project, `preview` and an
  out-of-place `dry` give byte-identical diffs, inventories and reports, in
  typed and untyped modes. This is the test that holds the claim.
- A preview leaves the project byte-identical, and leaves no temporary
  directory behind after success, failure or an interrupt.
- `preview` accepts every flag `dry` accepts; `--no-interactive` changes
  nothing.
- A refusal `dry` gives, `preview` gives with the same message and exit
  status.

## Open questions for the owner

- **Keep the quick listing?** Today's single-pass untyped listing is fast and
  sometimes all that is wanted. My recommendation is to keep it under an
  explicit flag, `--quick`, labelled as an upper bound on what `dry`
  applies, rather than as the default.
- **The output format** of the diff: unified diff by default, with
  `--stat` for a per-file summary.
