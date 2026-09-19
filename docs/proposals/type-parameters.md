# Proposal: preserve type relationships in extracted helpers

Status: deferred beyond 1.732; design proposal, not implemented.
Author: design note from the 2026-09 release audit

## Problem

Two blocks can share a computation while requiring a relationship between their
argument and result types. Taking a union independently for each helper parameter
forgets that relationship. Making the result another union does not restore it.

This example was checked during the release audit:

```python
def integers(left: int, right: int) -> int:
    result = left + right
    return result


def strings(left: str, right: str) -> str:
    result = left + right
    return result
```

Both functions type-check. This proposed shared signature does not:

```python
def shared(left: int | str, right: int | str) -> int | str:
    result = left + right
    return result
```

It permits the mixed argument combinations `(int, str)` and `(str, int)`, although
neither original block needs them. Its result also fails to promise `int` to
`integers` or `str` to `strings`. The needed contract relates the two inputs and
the result *within each call*:

```python
from typing import TypeVar

_T = TypeVar("_T", int, str)


def shared(left: _T, right: _T) -> _T:
    result = left + right
    return result


def integers(left: int, right: int) -> int:
    return shared(left, right)


def strings(left: str, right: str) -> str:
    return shared(left, right)
```

The audit ran these three complete programs through mypy 2.3.1 and Pyright
1.1.414. The originals and constrained generic version passed both. The union
version produced four mypy errors and three Pyright errors: invalid operand
combinations and incompatible returns. The original and constrained versions
also passed mypy 1.0.0 on Python 3.11.15. These are checker experiments on this
example, not evidence that every extraction can receive a generic signature.

Mypy documents constrained generic functions, and Pyright documents both their
consistent argument constraints and checking of the corresponding result type.
The proposed feature would infer a restricted class of such signatures; it
would not introduce a new type-system extension.
([Mypy](https://mypy.readthedocs.io/en/stable/generics.html#value-constrained-type-variables),
[Pyright](https://github.com/microsoft/pyright/blob/main/docs/type-concepts-advanced.md#value-constrained-type-variables))

## Constraints, bounds, and syntax

`TypeVar("_T", int, str)` and `TypeVar("_T", bound=int | str)` express different
contracts. A constrained variable is solved as one of its listed constraints;
subclasses can be promoted to that constraint. An upper bound admits subtypes
of the bound, including a union when the bound itself is a union. It is not a
substitute for requiring one of two alternatives consistently. Preserving the
exact subtype of a subclass and selecting a listed constraint are different
requirements; the inference must not silently exchange them.
([Python's TypeVar documentation](https://docs.python.org/3/library/typing.html#typing.TypeVar))

Use the explicit `TypeVar` declaration as the first rendering. It works on
Python 3.11 and newer and avoids requiring a newer checker merely to parse the
generated helper. Python 3.12 introduced this alternative:

```python
def shared[_T: (int, str)](left: _T, right: _T) -> _T:
    return left + right
```

Current mypy documentation supports both forms, and PEP 695 records Pyright's
support. New syntax should be an optional later rendering, gated by the target
project's minimum Python version and every participating checker's capability.
Running Towel itself on a newer Python is insufficient. `from __future__ import
annotations` does not make this syntax parse on Python 3.11.
([Mypy syntax compatibility](https://mypy.readthedocs.io/en/stable/generics.html#defining-generic-classes),
[PEP 695](https://peps.python.org/pep-0695/))

## Proposed first implementation

Start with fresh module-level helpers, fixed positional parameters, and a single
returned value. Infer at most one new constrained type parameter, with at most
four concrete alternatives, in the first implementation. Keep these limits
explicit and deterministic; extend them only with evidence from useful examples.
Keep a concrete signature when it already verifies. Otherwise try the bounded
generic candidate before the existing `Any` or bare-helper fallback variants.

1. Preserve the existing syntactic and semantic extraction checks. Type inference
   begins with a valid proposed helper and its actual call sites. It does not
   excuse a failed instantiation, evaluation-order, binding, or placement check.
2. Record each site's argument types and required result type together. A row
   belongs to one call site; do not independently union the columns and discard
   the rows. Retain the source and binding that justify each type.
3. Find candidate positions whose concrete types agree within each row and vary
   across rows. For the example above, the rows are `(int, int, int)` and
   `(str, str, str)`. Propose `_T` in those positions and retain concrete types in
   positions that do not participate. Equal observed columns are a candidate
   hypothesis, not proof of a universal relationship.
4. Initially require concrete, resolvable nominal types in those positions.
   Decline inference involving `Any`, `Unknown`, unresolved names, unsupported
   unions, existing unbound variables, overloads, or nested type constructors.
   Do not infer a subtype-preserving promise from constrained variables when
   constraint promotion would lose a caller's required subtype.
5. Ask the configured checkers to validate the generic helper body and all
   rewritten calls in the complete prospective project. A call-site matrix alone
   cannot prove that the body works for every permitted constraint. Keep only
   a signature that the checkers validate and that preserves each caller's
   required result type.
6. Allocate a fresh private type-variable name, its declaration, and its import
   with the helper. Treat these as one transactional proposal. Roll them back
   with a declined helper; include them in helper inventory and subsequent
   naming/import handling. Do not leave unused declarations after trying a
   different variant.

An unconstrained identity helper or a useful upper bound may deserve a later
rule. They should not be inferred by replacing a failed constraint set with
`object`, a union bound, or `Any`. Each rule needs its own body and call-site
obligations. This proposal does not attempt arbitrary relational or dependent
type inference, overload synthesis, or automatic invention of protocols.

## Placement, existing generics, and reuse

Type-variable identity is a binding, not a spelling. Two unrelated functions can
both use a variable named `T` without sharing that binding. An enclosing generic
class or function may already bind it. Pyright documents these scope distinctions,
and PEP 695 gives new-style type parameters their own lexical scope.
([Pyright scoping](https://github.com/microsoft/pyright/blob/main/docs/type-concepts-advanced.md#type-variable-scoping),
[PEP 695 scopes](https://peps.python.org/pep-0695/#type-parameter-scopes))

The initial inference rule should decline a candidate that requires moving or
rebinding an existing generic parameter. Later support must distinguish retaining
an enclosing parameter from introducing an independent helper parameter, preserve
bounds and constraints, and rename binders without capturing source names. A
module-level helper cannot simply refer to a type parameter scoped to a source
method. A declaration's constraint names must also resolve at its insertion
point; postponed annotations do not postpone an ordinary `TypeVar(...)` call.

Reusing an existing generic function is a separate operation. Its published
signature, bounds, constraints, and overloads remain unchanged. Verify the new
calls against that signature. If reuse fails, decline it or consider a fresh
extraction through the ordinary proposal machinery; do not generalize the
existing callee to make a new caller fit. Calls in unchanged modules remain part
of whole-project verification.

Methods need a later placement rule. In the Sphinx audit, a helper placed on
`ASTBase` received a `self` annotation restricted to two subclasses, which mypy
rejected. Replacing that annotation with a type parameter is not automatically a
valid method contract. Receiver bounds, inherited accessibility, and the host
class must be checked together before method inference is enabled.

## Containers, callables, and narrowing

Type relationships inside constructors need their declared variance. In
particular, mutable lists are invariant; `list[int]` cannot be treated as
`list[int | str]`. Read-only interfaces and mutable interfaces do not justify the
same substitutions. Later support for `list[_T]`, `Sequence[_T]`, tuples, or
mappings must preserve the original operations and independently verify both
argument acceptance and returned values.
([Typing specification: variance](https://typing.python.org/en/latest/spec/generics.html#variance))

Callable parameter types are contravariant and result types covariant. A union
of callables is not generally a callable taking union arguments. A constructor
signature is also insufficient evidence that a value is a class suitable for
`isinstance`; retain the distinction between `type[C]` and an arbitrary callable
returning `C`. Initially decline callable and class-object inference rather than
flattening either into a convenient signature.
([Typing specification: callables](https://typing.python.org/en/latest/spec/callables.html#assignability-rules-for-callables),
[Python's class-object types](https://docs.python.org/3/library/typing.html#the-type-of-class-objects))

The actual Sphinx refusal exposed a different boundary from the addition
example. Its original `ASTNestedName.__eq__` was:

```python
def __eq__(self, other: object) -> bool:
    if not isinstance(other, ASTNestedName):
        return NotImplemented
    return self.names == other.names and self.rooted == other.rooted
```

The paired `ASTCharLiteral.__eq__` compared `prefix` and `value`. The proposed
helper moved the guard into `ASTBase` and received delayed expressions:

```python
return self._extracted_func_0(
    ASTNestedName,
    lambda: self.names,
    lambda: other.names,
    lambda: self.rooted,
    lambda: other.rooted,
    other,
)
```

This preserves delayed evaluation but loses the caller's narrowing proof:
`other` is still `object` where these lambdas are defined. In a coherent snapshot
of Sphinx commit `e44a40eb2f810558ccd9da1425421270ccb81351` with the corpus's current
output overlaid, mypy reported four new attribute errors, one for each of
`names`, `rooted`, `prefix`, and `value`. All four remained when every helper
annotation became `Any`. The inferred variant also had the receiver-placement
error and an invalid constructor-`Callable` argument to `isinstance`; the bare
variant added strict-mode annotation/call errors. Sphinx's configured Pyright
rules suppressed those diagnostic categories, so mypy caused the refusal.

A type parameter on the helper cannot establish a guard in a different lexical
scope. Preserving narrowed captured variables requires retaining the guard before
the lambda is defined, or a separately justified change to the extraction
boundary or callback interface. Automatically adding casts, ignores, or `Any`
does not establish that proof. `TypeGuard`/`TypeIs` synthesis and such control-flow
transformations are outside the first implementation. The checker already has
conditions for retaining narrowing in closures; extraction must preserve them.
([Pyright captured-variable narrowing](https://github.com/microsoft/pyright/blob/main/docs/type-concepts-advanced.md#narrowing-for-captured-variables))

## Verification and fallback

The 1.732 release policy is established separately from this deferred proposal:
check the initial complete project before using checker-driven inference or
verification or writing refactored output. If that baseline contains type
errors, abort with a clear instruction to fix them or explicitly rerun with
`--no-types`. Do not silently disable checking. Existing source and callee
annotations remain unchanged. A checker execution failure is a distinct outcome
and must never certify a proposal as type-correct. An installation without an
available optional checker retains its existing explicit availability notice.

The untouched pinned Sphinx checkout, independently verified with clean tracked
git status, had 740 mypy errors and 22 Pyright errors through Towel's global
interpreter. Each diagnostic multiset exactly matched the coherent intermediate
graph after normalizing project paths. Pointing the checkers at the Sphinx test
environment for that intermediate graph reduced the counts to 691 and 20.
Missing stubs dominated mypy's diagnostics; the
remaining Pyright diagnostics concerned deprecated `contextmanager`
annotations. These results explain why an environment check is necessary; the
test environment is not claimed to be Sphinx's complete maintainer typing
environment. They do not invalidate the separately observed new lambda errors.

When checking is active, a generic candidate must leave the complete
prospective project clean under every selected checker. Include all modified
files together and unchanged consumers, preserve configured targets/exclusions,
and map copied output to the original logical project paths. A timeout or
malformed checker response is not a proof. The existing semantic checks and
behavioral tests remain necessary because annotations do not establish runtime
equivalence.

If a candidate generic signature fails, retain an already verified ordinary
signature when one exists, try the existing permitted fallback variants only
under their usual checks, or decline the proposal. Do not call a fallback a
successful generic inference. A run explicitly using `--no-types` bypasses the
checker and must not synthesize these new generic contracts from an unverified
hypothesis.

## Phases and acceptance tests

1. **Capture evidence and report candidates.** Represent per-site type rows and
   binder identities explicitly. Report a bounded inference candidate without
   changing generated code. Keep the successful addition example and the Sphinx
   refusal as distinct fixtures.
2. **Fresh module-level constrained helpers.** Implement the single-variable rule
   above using Python 3.11-compatible `TypeVar`. Add declarations/imports to the
   proposal and transactional plan. Enable only after both checker validation and
   behavioral tests pass.
3. **Existing generic bindings and structured positions.** Add one supported
   family at a time, with binding-aware scope rules and explicit variance
   obligations. Method placement, nested generics, and callable relationships
   require their own designs; they are not implied by phase 2.
4. **Optional newer syntax and corpus evaluation.** Gate PEP 695 rendering on
   project/checker support. Measure accepted transformations, refused candidates,
   added checker cost, and readability on the pinned ecosystem corpus before
   changing defaults.

The acceptance suite should test the obligations, not merely the generated text:

- Both checkers accept the original addition functions and generated generic
  version, preserve `int`/`str` results, and reject the mixed call `shared(1, "x")`.
  Exercise extraction with `min_lines=2` for the two-statement example. Runtime
  examples produce the same values and exceptions as the originals.
- The independent-union and union-bound alternatives do not accidentally pass
  as equivalent contracts. A subclass fixture detects constraint promotion when
  a caller requires the precise subclass.
- A body invalid for one constraint is rejected even when its observed callers
  do not reach the problematic branch. Unrelated argument columns do not become
  falsely correlated.
- Repeated type-variable spellings in different scopes, nested generic scopes,
  shadowed `typing` imports, and cross-file helper placement preserve bindings or
  are declined. Existing generic callees keep their signatures.
- Mutable-container writes and callable input/result variance expose unsafe
  substitutions. Class arguments to `isinstance` retain their class-object type.
- The Sphinx guard/lambda shape remains declined on a clean reduced fixture
  unless a future, separately verified transformation preserves the narrowing.
  Adding a `TypeVar` alone must not make the test pass by suppressing errors.
- A new error in an unchanged consumer, disagreement between checkers, a checker
  timeout, and an initially failing project exercise the whole-project policy.
  An initially failing project aborts before creating copied output or modifying
  existing files; rerunning the same command with `--no-types` is possible.
  No annotation or import is left behind by a failed variant.
- Generated legacy syntax parses and runs on Python 3.11 and passes the tested
  checker floor. New syntax is never emitted for an unsupported project or
  checker. Re-running Towel reaches the expected fixed point without accumulating
  fresh type-variable declarations.

The [architecture references](../ARCHITECTURE.md#references) explain the
anti-unification background for finding shared code. They do not supply the
typing argument: preserving a syntactic instance and preserving a polymorphic
contract are separate obligations.
