# Proposal: reuse an existing function instead of extracting a redundant helper

Status: implemented for the whole-body case (2026-09-17) and withdrawn on
2026-09-23 (commit c4bf39e): a function rewritten to call another looked that
one up in its module at every call, so patching or rebinding it changed both
(audit r06). Every whole-body site now calls one new helper, and
`reuse_existing_functions` has no effect. The general
index-by-signature form below would meet the same objection.
Author: design note from the 2026-09 audit sessions

## What was implemented

`_redirect_to_existing_function` (the `ExistingFunctionReuse` mixin in
`unification/reuse.py`, part of the engine) runs after a
proposal is fully built and verified. When one replacement site is the entire
body (after the docstring) of a plain module-level function, the generated
call there passes each of that function's positional parameters exactly once,
and every other argument is a name the function reads from its own module,
the helper applied to any arguments is that function applied to them. The
proposal is rewritten: the function is left as it is, the fresh helper is
dropped, and every other site calls the function with its arguments in the
function's parameter order. Ambient arguments are dropped only when the site
binds the same name to the same module-level definition, or to an identical
absolute import in another module. Since 2026-09-18 a same-module helper
reads such names bare, so the ambient mapping matters only across files.
Candidates are tried in source order; one
that a site cannot reach by name (shadowed, or mangled inside a class), or
whose import would close a cycle, is skipped, and with no viable candidate the
ordinary extraction stands. Decorated, async, variadic, conditionally defined,
`global`-rebound, or module-`del`eted functions are never targets. Set
`reuse_existing_functions=False` on the engine to disable it.

This covers the motivating cases (two identical functions; a block that
restates an existing function) without an index: the pair search already
generates the whole-body block of every function, so the match surfaces as
an ordinary pair. What it does not cover is a body that matches an existing
function *the pair never touches* in the same iteration; the fixed-point loop
reaches it on a later iteration when that function's whole body pairs with
the new helper.

## Follow-up idea: re-rolling an unrolled recursion (not implemented)

A function may contain a fragment that is an instance of its own body: the
recursive step written out once more, with the parameters replaced by
expressions such as ``n - 1``. The same check as above, run on the enclosing
function itself, would find a block inside ``f`` that unifies with the whole
body of ``f`` under a substitution of ``f``'s parameters, and replace the block
with ``f(<substituted arguments>)``; mutual recursion is the same check
against the other function's body. Only a *syntactic* instance qualifies,
and branch-guided unfolding counts as syntactic. In ``factorial`` written as
``if n == 0: 1 / elif n - 1 == 0: 1 / else n * factorial(n - 1)``, the
``elif`` test is the body's own test instantiated at the recursive argument
``n - 1``, which unification recognizes. Unfolding ``factorial(n - 1)`` one
step under that test gives ``n * 1`` for the ``else`` branch, still syntactic;
comparing it with the ``elif`` branch's ``1`` then needs ``n == 1`` from the
test, an arithmetic fact. Had the branch been written ``return n * 1`` the
whole reduction would be syntactic. The pair search never generates such a
pair today because the whole body contains the fragment, so this needs a
dedicated per-function pass.

## Original proposal

## Problem

When Towel finds that a duplicated block's body matches an **existing named
function**, it still extracts a brand-new helper and rewrites every duplicate —
including the existing function — to forward to it. Given two identical
functions:

```python
def alpha(value):
    tmp = value + 1
    total = tmp * 2
    return total

def beta(value):
    tmp = value + 1
    total = tmp * 2
    return total
```

Towel produces a redundant helper plus two thin forwarders:

```python
def __extracted_func_0(value):
    tmp = value + 1
    total = tmp * 2
    return total

def alpha(value):
    return __extracted_func_0(value)

def beta(value):
    return __extracted_func_0(value)
```

Both original functions become pure forwarders — the same trivial-forwarding
shape the `skip_trivial_helpers` filter rejects when it is the *extracted*
helper, but here it is the *rewritten original*, so the filter does not catch
it. A human would instead keep one function and have the other call it:

```python
def alpha(value):
    tmp = value + 1
    total = tmp * 2
    return total

def beta(value):
    return alpha(value)
```

This was observed directly while dogfooding: Towel proposed a third copy of the
match-pattern capture logic that already existed as both
`scope_analyzer.pattern_capture_names` and a private copy in
`definite_assignment` (since removed in favour of the former),
rather than routing the duplicates through the existing function. The audit
fixed that particular case by hand.

## Proposed behavior

When a proposal's helper body unifies with an **existing** function's whole body
(up to parameter renaming), and that function is reachable from every duplicate
site, redirect: drop the new helper and rewrite each duplicate to call the
existing function, mapping the block's free variables to that function's
parameters. Fall back to ordinary extraction when there is no match or the
target is not reachable.

## Why it fits the existing design

The machinery is largely in place:

- `block_signature.extract_block_signature` and `quick_filter` already index and
  pre-filter blocks by structure; indexing every analyzed function by its
  body signature reuses this.
- `Unifier` already decides structural equality up to parameter renaming, which
  is exactly the match needed between a duplicate block and a candidate
  function's body.
- `_cluster_candidate_call` already tests whether an additional occurrence can
  share an extracted helper; a sibling path would test whether it can call an
  existing function instead.
- The cross-file import generator already computes reachability and relative
  imports between two files, which the redirect needs to reach the target.

## Sketch of the work

1. Build an index of analyzed functions keyed by body signature (skip functions
   with decorators, closures over enclosing scope, or receiver-dependent bodies
   that a plain call could not reproduce).
2. Before finalizing an extraction proposal, look up the helper body's signature
   in that index. On a hit, verify with the unifier that the bodies are
   equivalent up to parameter renaming and that the candidate's parameters can
   be supplied from the site's free variables.
3. Verify the candidate function is reachable from each site (same scope, or
   importable — reuse the import-path logic and its relative-import fallback).
4. Emit a "call existing function" change instead of a helper-extraction change:
   no new definition, each duplicate becomes a call with mapped arguments.
5. Preserve the soundness invariant: instantiate the candidate with each site's
   arguments and confirm it reproduces the original block, exactly as extraction
   already verifies its generated helpers.

## Testing

- Hostile and observational-equivalence fixtures for: two identical top-level
  functions; a block matching a function in another module (reachable via
  import); a near-match that must fall back to extraction; an unreachable target
  (private, closure, receiver-bound) that must fall back.
- An ecosystem-check run, since this changes what real projects produce.

## Risks

- Reachability and argument mapping are the subtle parts; a wrong redirect would
  change behavior. The per-site instantiation check is the backstop.
- Calling an existing function couples modules that were independent; the
  redirect should respect the project's import layout and never introduce a
  cycle (the import generator already refuses cycles).
