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

## Found after the deferral

These were found by the same round's real-code and semantic auditors, and
deferred on the same grounds.

| Decline | What is over-broad | Evidence |
|---|---|---|
| Blocks differing only in an operator | `n + 1` against `n - 1` is `unification_failed`, where any other differing subexpression is parameterised. The same holds for `+`/`*`, `<`/`>`, `not`/`-` and `and`/`or`. | semantic reproducer `p2_decline_differing_operator` |
| `global` declared at one site only | `bound_after_block` ignores `global`, so the pair is `incomplete_lifetime_block2`. | `p2_decline_global_declared_in_one_site` |
| A hole conflated with a binder | For `xs[v1 % 4]` against `xs[len(xs) % 4]`, every `v1` at site 2 is replaced. The instantiation check declines it safely, and only a smaller tail is extracted. | `p2_decline_hole_conflated_with_binder` |
| `global` declared at both sites | A helper with its own `global` would be sound; it is declined as `module_data_lookup` and `moves_scope_declaration`. | semantic harness `misc_global_in_both` |
| A per-module constant under `--cross-module` | A thunk would be sound. It is common: every module-level `logger`. | semantic harness `bind_x_global_both` |
| A name used only in a local-variable annotation | Such an annotation never runs, but the name is treated as a run-time read. It declines the pair with a `TYPE_CHECKING` import, and otherwise makes the type a helper parameter. | real-code reproducer `p2_xmod_annotation_only_name` (uvicorn) |

## Found in round 4

These were found by round 4's decline auditor and deferred on the same
grounds. Each is a decline, never a wrong transformation. The first two are
places where Towel does not look at all, rather than looks and declines; they
are the next release's first candidates. Reproducers are in the round-4
evidence, under `reading/repro/`.

| Decline | What is over-broad | Evidence |
|---|---|---|
| Differing expression kinds in a list field | `_unify_lists` gives up whenever list elements differ in node type (`unifier.py`, `_unify_lists`): `load(n)` against `load(n*2)` or `load(n.value)`, `[n, 1]` against `[n+1, 1]`, `a and b` against `a and f(b)`, comparators, dict values. The same difference in a single-valued field is parameterised. The auditor's patch: 37 of 108 pairs fewer declined, and 3 more refactorings on packaging, click, rich and pygments. | `p2_defect_list_element_kinds_never_unify` |
| `match` case bodies and `except*` bodies | They are never enumerated as block sites. The enumerator walks `body`/`orelse` and handles only `ast.Try`, so a duplicate under `case` or `except*` is never considered, while the same code under `except` is extracted. | `p2_defect_match_case_bodies_never_enumerated`, `p2_defect_except_star_handlers_never_enumerated` |
| A block containing `await`, `async for` or `async with` | `frame_sensitive_block`. An `async def` helper awaited at each site would be sound. It is routine in async code: 904 of 1,168 frame-sensitive pairs on anyio, httpcore and starlette. | `p2_overbroad_await_block` |
| Differing module data within one module | `x + LOW` against `x + HIGH`, two constants never rebound, is `module_data_lookup`. An eager argument or a thunk would be sound. | `p2_overbroad_same_module_data` |
| A subscripted project generic base | `class IntBox(Box[int])`, including PEP 695 spellings, puts the class under `class_machinery_may_transform_methods`. Only `typing.Generic` and the listed bases are accepted subscripted, and the culprit is reported vaguely as `bases of IntBox`. 176 pairs by default, 3,624 across modules; no refactorings lost on the four packages. | `p2_overbroad_subscripted_project_base` |
| `type` as a base | The methods of a metaclass are declined: 67 pairs by default, 2,213 across modules. | `p2_overbroad_type_as_base` |
| Standard-library bases not yet verified | `typing.NamedTuple` (29 / 1,037 pairs), `io.*` (106 / 2,364) and `email.policy` (1 / 13). Each needs the same verification as `known_bases.py`'s entries before it is listed. | `p2_overbroad_namedtuple_base` |
| Project metaclasses | A project decorator can qualify as a plain wrapper; a project metaclass never can. Moving code out of a method into a module function is sound for every pygments lexer method. Hosting a helper in a method the metaclass rewrites, as `LexerMeta` makes `analyse_text` static, is not, so the allowlist's caution is warranted for hosting. This is the 27-of-31 pygments cost. | round-4 pricing of pygments |
| A lambda passed to a method | `ys.sort(key=lambda v: -v)` is `created_object_escapes`. It is documented under the qualname rule. | `probes/p_sort_key_lambda_method` |
| A partial-return block | A block that returns on some paths only; a sentinel would do. | reading report, enumeration drops |

**The cost of the decorator and machinery rules together** depends heavily on the
project. Round 4 measured 33 of 93 refactorings (35%) by default and 39 of 119
across modules, over packaging, click, rich and pygments. Most of it is
pygments' project metaclasses. The ten-project figure DECISIONS records, 49 of
721, is the better estimate of the typical cost; the four-package figure is the
cost on a metaclass-heavy codebase.
