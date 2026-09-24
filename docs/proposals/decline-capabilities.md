# Declines that need a wider analysis

Status: deferred to the release after 1.772, by the owner on 2026-09-24
(see `docs/DECISIONS.md`). Each item below is a defect by the owner's
standard: a decline that exists only because Towel's analysis cannot yet
show a sound transformation sound. None is a limitation to document and
leave. Each would be fixed by making the safety analysis accept code it now
refuses, so each needs its own soundness argument, tests that fail before
and pass after, and a from-scratch audit of its own.

## How the costs were measured

The costs come from the third audit round of 1.772, at `077a712`, on
packaging 26.3, click 8.5.0, rich 15.0.0 and pygments 2.21.0, taken from a
venv's site-packages.

- **Pairs.** A pair count ("d / x") is the number of candidate pairs one
  analysis declined for that reason, summed over the four packages: first
  in the default mode, then with `--cross-module`. The blocks of one
  function pair overlap many times over, so a pair count is not a
  refactoring count.
- **Refactorings.** Where a reason could be switched off by a patch, the
  change in applied refactorings is given too, from `dry --no-types
  --no-format`.
- **Evidence.** The audit report and its reproducers (`d1` to `d9`) are kept
  in the release handoff's evidence directory.

## Safety analysis

| Decline | What is over-broad | Cost |
|---|---|---|
| `rebound_external_binding` after `getattr(<expression>, ...)` | Any `getattr` whose first argument is not a name marks the whole module reflective, so every external read of every block counts as a hazard, builtins included. `getattr` rebinds nothing. | click 8 → 10, rich 22 → 23; with `--cross-module`, click 8 → 10 and rich 28 → 29 |
| `nested_binding_escapes` | There is no liveness analysis for nested blocks. Returning every name the block binds that is read anywhere after it, when that name is definitely bound, would be sound. | 4,021 / 69,941 pairs, the largest share |
| `unsafe_reassignment_block1` / `_block2` | Rebinding a name bound before the block is dead after a `return` in a value-producing block. Elsewhere, `x = helper(x)` is the standard form. | 2,452 / 27,341 pairs; a naive patch recovers 1 refactoring, a lower bound |
| `value_producing_mismatch` | It fires when neither block returns and only the variables read afterwards differ. | 773 of the 844 default-mode pairs have no `return` in either block |
| `nonlocal_safety_skip` | It fires for a block that never touches the nonlocal name. | reproducer `d3` |
| `private_name_lexical_class` | It checks the whole function rather than the block: a block with no private names is declined because another line of its method uses `self.__secret`. | reproducer `d4` |
| `cross_module_global_declaration` | Any `global` anywhere in a participating module declines the pair. | 0 / 9 pairs |
| `module_data_lookup` across modules | Module data that nothing ever rebinds is still declined. | 14 / 143 pairs |
| `import_time_effects` | A project metaclass counts as code that runs. | 0 / 501 pairs (pygments lexers) |
| `impure_eager_parameter`, `undefined_names_in_call` | These safety nets fire where a builtin table is incomplete; `iter` is missing from `CALL_ARGUMENT_BUILTINS`, for example. | 31 / 246 pairs |

## Which blocks are enumerated

| Decline | What is over-broad | Cost |
|---|---|---|
| A value-producing block without the return-or-if/else shape | A block that exits on every path but ends in `raise`, `try` or `match` is never enumerated, so its decline is never traced. | reproducer `d2`; not counted |
| A block holding a nested `def` or class | A nested function used only inside the block is not enumerated. The same code written with a lambda is. | reproducer `d7`; not counted |

## Unification and rendering

| Decline | What is missing | Cost |
|---|---|---|
| The constant-consistency invariant | `n + 1` against `n + 2` is declined whenever `2` also occurs elsewhere in the block, although the instantiation check verifies the helper without the invariant. The decline is traced as `unification_failed`. | packaging +1; with `--cross-module`, packaging +1 and rich +2 |
| `unification_failed`, other capabilities | Slices, starred items, f-strings, lambda parameters of special kinds, and walrus targets cannot be parameterised. | within 4,031 / 42,729 pairs |
| `frame_sensitive_block` | `yield` needs `yield from` delegation, and `break` or `continue` out of the block needs a way to report it. A comprehension walrus that is not read outside the comprehension is declined as well. | 2,762 / 51,046 pairs; pygments has 1,335 `yield` and 98 `break`/`continue` |
| `host_has_stub` | The stub could be extended instead. | 0 / 0 pairs |

## Policy, to be decided with the owner

| Decline | Question | Cost |
|---|---|---|
| `not_structurally_similar`, at a similarity threshold of 0.6 | It blocks pairs that pass every check. The recovered helpers read in rich bundle local aliases, which is low value. | default mode 93 → 104 refactorings over the four packages; with `--cross-module`, 119 → 142 |
| `narrowing_lost_at_call_site` | Under `--no-types`, and in unannotated code, it changes no behaviour. | 2 / 27 pairs |
