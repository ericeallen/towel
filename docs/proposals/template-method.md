# Proposal: form a Template Method instead of extracting a free helper

Status: idea recorded 2026-09-18; not designed, not implemented. Research
needed before design (see *What we need to find out*).
Author: design note from the 2026-09 audit sessions

## The observation

Run on its own source, Towel proposes extractions whose helper would be
correct and would hurt the code. The clearest cases are the AST visitors: a
`visit_ClassDef` in one `NodeVisitor` subclass and a `visit_FunctionDef` in
another share the shape *do something, descend, undo it*; a `visit_For` and a
`visit_comprehension` share *bind the target, visit the parts*; a
`_visit_loop` in one analysis and a `_visit_loop_with_bindings` in another
share *iterator, target, body, else*. Anti-unification finds these because
they really are one structure with a few varying sub-expressions. The
helper Towel would write is a module-level function taking `self` and the
varying pieces as parameters, called from each method. Nobody would write
that by hand, and the audit pass declined every one of these proposals for
that reason.

What a programmer writes instead is the Template Method pattern: the shared
structure becomes a method of an abstract superclass, the varying pieces
become hook methods the superclass calls, and each original class becomes a
subclass that implements the hooks. The 2026-09-18 refactoring of Towel's
visitors did this by hand and produced three bases in `visitors.py`
(`OwnScopeVisitor`, `DefinitionDepthVisitor`, `ScopeVisitor`) with eighteen
subclasses. The proposal is to let Towel produce that shape mechanically.

## Why it is the same computation as extraction

The anti-unifier already computes everything the pattern needs:

- The **template** is the least general generalization of the blocks: the
  statements they share, with the differing sub-expressions abstracted. In
  the pattern it is the body of the template method.
- Each **parameter** of the substitution is a differing sub-expression with
  one value per block. In the pattern each becomes a **hook**: an abstract
  method of the superclass whose implementation in subclass *i* returns, or
  performs, that block's expression. Where the differing expression is
  already a call on `self` (`self._record(node)` against
  `self.class_stack.append(node.name)`), the hook is that call; where it is
  a plain value, the hook returns it.
- The **receiver** is `self`, which the pattern gets for free, so the
  awkward explicit receiver parameter of a module-level helper disappears.
- The **call sites** are the original methods; each becomes the subclass's
  hook implementations, and the method itself is inherited.

So the difference from extraction is not in what is computed but in how the
result is rendered and where it lives: a class in the hierarchy rather than
a function in the module. The instantiation check applies unchanged: the
template method with subclass *i*'s hooks substituted must reproduce block
*i* up to renamed binders.

## When it applies

The pattern is the right rendering only under conditions that extraction
does not need, and each must be decided, not assumed:

1. **The blocks are methods**, or the whole bodies of methods, of classes
   that share a base (`ast.NodeVisitor` here) or can be given one.
2. **The varying pieces are naturally hooks.** A sub-expression that reads
   or calls through `self` is; a literal is a hook that returns a constant,
   which is fine; a free variable of the enclosing function that is not an
   attribute of `self` is not, unless it is passed to the template method
   as an ordinary parameter. The mix decides between a pure template method,
   a template method with parameters, and ordinary extraction.
3. **Evaluation order survives.** A hook call is a call, so a differing
   expression that the block evaluated conditionally or repeatedly must
   become a hook *invoked where the expression stood*, which is what thunks
   do today; the hook is the thunk with a name and a `self`.
4. **The hierarchy admits a new class.** Inserting an abstract base between
   `ast.NodeVisitor` and the two visitors changes their MRO. When the
   classes already have different intermediate bases, or use multiple
   inheritance, the safe placements are limited or none.
5. **Names do not collide.** The hook names must be fresh in every subclass
   and in every class between them and the new base, and must not shadow a
   method a subclass defines for another purpose; `super()` calls in the
   original methods must still resolve to what they resolved to.
6. **Identity and introspection.** Code that inspects `type(x).__mro__`,
   pickles instances by class path, or registers classes by name can
   observe the new base. This is the same class of limitation as frame
   sensitivity and needs the same treatment: reject what can be detected,
   document what cannot.

## Why it is worth doing

The self-run is the evidence: of the eighteen proposals Towel made on its
own source of 2026-09-18, after the audit (a fixed-point `towel dry
src/towel` applies fifteen at `5ff2458`, September 19, 2026), at least ten
were visitor pairs of this kind.
Real projects are full of visitor, handler, and strategy hierarchies where
the shape repeats across sibling classes. Today those duplications are
either left (the trivial-helper filter and the reviewer's judgment decline
them) or extracted into a function that a reviewer then rejects. A rendering
that produces the class a programmer would have written turns a proposal
that hurts readability into one that improves it, and the review-first
workflow stays as it is: the proposal is previewed, named, and tested.

## What we need to find out

This is a design that needs reading before writing. Questions to answer,
with where to start:

- **The refactoring literature.** *Form Template Method* is a named
  refactoring in Fowler's catalogue and in Kerievsky's *Refactoring to
  Patterns*; both give the manual procedure and its preconditions. Opdyke's
  thesis gives the behavior-preservation conditions for the primitive
  refactorings it composes (add class, move method, rename method, pull up
  method). We should reproduce their preconditions as guards and see which
  of Towel's existing checks already cover them.
- **Clone detection in object-oriented code.** Work on detecting *type-3
  clones across sibling classes* and on recommending "pull up" refactorings
  (the Eclipse and IntelliJ implementations, and the JDeodorant line of
  research on detecting Template Method opportunities) has faced the
  placement and naming questions above. We should learn what they found
  hard.
- **How to score the two renderings.** Given a pair that admits both a free
  helper and a template method, which to propose? Candidates: the number
  of `self`-relative parameters, whether a common base already exists,
  whether the varying pieces are all calls on `self`. This needs a corpus
  of judged examples, and the ecosystem corpus supplies one: run the
  detector, render both, and have a reviewer say which they would accept.
- **Hook naming.** Generated hook names are the `__param_0` problem again;
  the `rename-helpers` inventory and the naming workflow would need to
  cover methods of a generated base class, and the assistant needs to see
  the two implementations to name the hook well.
- **Interaction with clustering and reuse.** A third sibling class with the
  same shape should become a third subclass, which is clustering; a base
  class that already has the template method should be reused rather than
  duplicated, which is the existing-function reuse in a new guise.
- **Verification.** The instantiation check needs a variant where the
  substitution is applied through method dispatch: substitute each hook's
  body for its call in the template and compare against the block. Where a
  hook body is more than an expression (a differing statement sequence),
  the comparison is on statements, which the check already handles for
  helpers that return values.

## Plan, once the research is in

1. A detector that recognises, among accepted pairs, those whose blocks are
   whole method bodies of classes with a common base and whose parameters
   are all `self`-relative or constant. Report them in `preview` as
   "template method candidates" without rendering anything.
2. Run the detector over the ecosystem corpus and Towel's own source; keep
   the candidates as fixtures with a hand-written expected rendering, the
   way the hostile batteries pin engine behavior.
3. Render: the abstract base with the template method and abstract hooks,
   the subclasses' hook implementations, and the base-list edit, as one
   proposal applied through the existing transactional plan. Verify by the
   dispatch variant of the instantiation check and by the project's own
   suite in the ecosystem check.
4. Only then decide the scoring between the two renderings, with the judged
   corpus from the research.

Nothing here changes what Towel extracts today; the proposal adds a second
rendering for a class of duplicates that the first rendering serves badly.
