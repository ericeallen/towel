# Dethunking: where the remaining `lambda` arguments come from

Status: evidence gathered 2026-09-17 on the 1.618 outputs; item 2 was
resolved differently on 2026-09-18 (see below); items 1, 3 and 4 remain
open. The exact, meaning-preserving improvements left are small; the large
buckets need an assumption the engine does not make today.

## What a thunk is for

When the sub-expression that differs between duplicates is more than a name,
literal, or tuple of those, Towel passes it as `lambda: <expr>` and the helper
calls it where the original expression stood (`semantic_safety.is_eagerly_
evaluable`, `defer_impure_parameters`). Evaluating it eagerly at the call
would change *when*, *whether*, and *how often* it runs. A thunk the helper
evaluates first, once, and unconditionally, before any effect, is already
inlined back into a plain argument (`thunk_inlining.inline_leading_thunks`).

## Census of the 1.618 ecosystem outputs (91 projects)

Helper calls: 4,695, of which 1,027 pass at least one lambda. By the shape of
the deferred expression (one row per lambda argument):

| Count | Shape | Example |
|---|---|---|
| 921 | `name.attr` | `lambda: self._on_completed_fut` |
| 145 | bare name | `lambda: message` |
| 80 | `name(...)` call | `lambda: CapacityLimiterStatistics(...)` |
| 76 | comparison | `lambda: self._trio_socket.fileno() < 0` |
| 47 | `expr.attr` | `lambda: self._semaphore.value` |
| 43 | mutable display | `lambda: []`, `lambda: {}` |
| 42 | constant | `lambda: 0`, `lambda: None` |
| 41 | binary operator | `lambda: '--' + data` |
| 86 | method calls (`x.m()`, `self.m()`, `expr.m()`) | `lambda: value.as_tuple()` |
| 35 | subscript | `lambda: node[0]` |
| 28 | unary operator | `lambda: not G.is_directed()` |
| 25 | tuple / boolean operator | `lambda: (node, 'text')` |
| 16 | forwarded callee `lambda *a, **k: f(*a, **k)` | `MsgpackConverter` |

By *why* the helper could not inline the thunk (one row per helper parameter,
computed by replaying `inline_leading_thunks`'s prefix walk over the emitted
helpers):

| Count | Reason |
|---|---|
| 174 | evaluated once, unconditionally, but after the prefix ended (a preceding `if`/loop/`with`) |
| 161 | evaluated only under an `if` |
| 156 | in the prefix, but after an effect (an attribute read or call the helper runs first) |
| 115 | evaluated only under a short-circuit boolean operator |
| 60 | in the prefix, but used more than once |
| 11 | in the prefix, but out of parameter order |
| ~35 | under a loop, `try`, `with`, or conditional expression; or multi-use elsewhere |

## Exact improvements still available (small)

1. **Parameter order equals evaluation order** (open). The extractor lists
   unified parameters in discovery order, then free variables alphabetically.
   When two leading thunks are evaluated in the opposite order, inlining
   stops (11 parameters). The helper's parameter order is Towel's to choose,
   so ordering parameters by first evaluation in the template would remove
   this case.
2. **Plain-name callees need no forwarding lambda.** Resolved 2026-09-18 by
   declining the pair by name (`forwarded_callee`) rather than by inlining a
   bare-name callee; the 16 forwarded-callee lambdas in the census are now
   16 declined pairs. Passing a bare-name callee as a value remains a
   possible refinement.
3. **Constants and tuples that share a parameter with an impure site** (42 +
   13) cannot be dethunked at that site alone: the helper is shared, so the
   parameter is a thunk at every site.
4. **Bare names** (145) come from the uncertain-binding path
   (`_thunk_uncertain_free_variables`): a local bound on only some path before
   the block is read as a thunk so an `UnboundLocalError` is raised where the
   original raised it. Each is worth inspecting; some may be definite bindings
   the analysis does not yet recognize. Since the module-name rule of
   2026-09-18, a shared name both sites resolve at module scope is read bare
   by a same-module helper and is no longer in this bucket; the remaining
   bare-name thunks are locals bound on some path, enclosing-function cells,
   and cross-file module names.

Together the exact items are on the order of 2% of the lambdas emitted.

## The large buckets need an assumption

Nine of every ten remaining lambdas defer an attribute read, a call, a
subscript, or an operator that the helper evaluates after some effect,
conditionally, or repeatedly. Hoisting any of them is a change of meaning
unless the expression is pure and total: reading `self._x` can run a property,
can raise `AttributeError` on the path where the original never read it, and
can see a different value once the effects before it have run. Nothing in the
program text establishes those properties for arbitrary attributes.

The one way to remove most of the 921 `name.attr` thunks is an opt-in policy:
*treat attribute reads on a parameter as pure and stable across the block
unless the block assigns that attribute.* That is a plausible convention in
most codebases and a wrong one in any that use properties with effects or
mutate `self` through a method called earlier in the block. It would have to
be off by default and named for what it assumes (for example
`--assume-plain-attributes`), and the equivalence batteries would need
fixtures that violate it so the flag's blast radius stays visible.

## Recommendation

Implement items 1 and 2 when convenient; they are exact. Investigate the 145
bare-name thunks for definite-assignment precision. Do not weaken the
verified default; if readability of `self._x` thunks matters enough, add the
assumption as a named opt-in and measure it on the corpus.
