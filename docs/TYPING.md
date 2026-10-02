# Type checking and helper annotations

[Documentation index](README.md)

Towel annotates helpers in code that already uses annotations. It combines
what callers declare with what the project's checker can infer, then checks
the proposed helper together with its rewritten calls and unchanged consumers.
Caller-side type narrowing stays at the call site.

On this page:

- [Choosing a checker](#choosing-a-checker)
- [Checking a transformation](#checking-a-transformation)
- [Code the checker cannot verify](#code-the-checker-cannot-verify)
- [Constructing annotations](#constructing-annotations)
- [Generic helpers and signature fallbacks](#generic-helpers-and-signature-fallbacks)
- [Running without verification](#running-without-verification)

## Choosing a checker

Install the optional tools with `pip install "code-towel[types]"`.
Towel follows the project's configuration:

| Project configuration | Behavior |
| --- | --- |
| mypy | mypy infers and verifies types. |
| Pyright | Pyright infers and verifies types. |
| Both | mypy infers; both verify. A new error from the first checker rejects the candidate without asking the other. |
| Neither | Use an installed checker, preferring mypy. |

A configured checker must be installed where Towel runs. Otherwise the typed
run refuses before writing anything and names the missing checker. Install
it, or explicitly choose [unverified extraction](#running-without-verification).

With no mypy configuration, verification uses mypy's defaults, as a direct
mypy check of the same files would. Unannotated function bodies go unchecked,
and imports without available types are errors. Inference alone looks inside
unannotated bodies to obtain more than `Any`.

### Plugins and project configuration

Configured mypy plugins, including django-stubs, pydantic and SQLAlchemy
plugins, run in every check. They execute project-configured code. If a plugin
cannot load, the typed run refuses before writing and includes mypy's error.

Run Towel in an environment with the dependencies and checker setup the
project needs. See [checker setup in the API guide](USAGE_GUIDE.md#formatting-and-type-checker-setup)
when constructing an engine directly.

## Checking a transformation

Before changing anything, Towel checks the original project and reports its
existing errors. Those errors form the baseline. A proposed change must not
introduce an error the baseline cannot account for.

All prospective changed modules are checked together with unchanged consumers.
Once a change is accepted, its result becomes the baseline for the next
change. A run verified through the Pyright language server also receives a
fresh command-line Pyright check at the end.

### How existing errors are matched

The comparison accounts for movement without treating any error of the same
kind as interchangeable:

- In untouched files, an error must retain the same message and line.
- On untouched lines of a changed file, it must retain the same message at
  the corresponding line after movement.
- In a new helper, it must correspond to the same statement in one original
  copy of the extracted block. Each error from that copy accounts for one
  error; errors from the other copies are not spare allowances.
- At a rewritten call site, it must correspond to the lines the call replaced.

Messages containing line numbers, such as `already defined on line 12`, may
change when code moves. Towel then treats the message as new and declines the
change. This conservative rule prevents a moved baseline error from hiding
a new one. The final cold check uses the same comparison.

### Preserving narrowing

A guard or assignment that establishes a refinement used later by its caller
stays in the caller. Extracting the guard into an ordinary helper would hide
that fact from the checker. Towel looks for smaller valid extraction windows
around these boundaries instead of repeatedly checking a proposal that cannot
preserve the caller's types.

The intent is both to save typed validation time and to retain useful
extractions. The [design decisions](DECISIONS.md) record this requirement;
declining every proposal would not satisfy it.

## Code the checker cannot verify

### Untyped imports

A file is left unchanged when the checker cannot type a name it imports, for
example because an import cannot be resolved or its stubs are missing. Towel
reports these files. Install the missing dependencies or stubs to make typed
extraction possible there.

Towel asks every configured checker what each import binds. Suppressing the
usual diagnostic with `ignore_missing_imports` or Pyright's
`reportMissingImports` does not make an untyped import verifiable. Similarly,
a subtype question involving a type seen as `Any` remains unanswered.

### Unreachable and excluded code

A checker may consider code unreachable because of its target platform or
Python version, or because declared types rule out a branch. Examples include
`if sys.platform == "win32":` while checking for Linux, or a fallback after an
`isinstance` test the annotated argument always passes.

Towel probes which regions each checker actually sees. It reports unseen
regions and declines any transformation that writes a line outside some
applicable checker's coverage.

Configuration exclusions are different: a file suppressed by a checker's
configuration, such as Pyright's `exclude` or `ignore`, is outside that
checker's check. Other configured checkers may still verify it. If none
reports on the file, Towel treats it like an unannotated function body: it
copies declared annotations, but does not infer annotations no checker would
verify.

### Checker failures

A checker crash, timeout or plugin-loading failure refuses the typed run.
It does not silently switch to untyped extraction. Fix the checker setup or
make the explicit choice to use `--no-types`.

## Constructing annotations

### Parameters and return values

If every argument for a helper parameter is an annotated, never-rebound
parameter of its caller, Towel uses that declaration. Otherwise it asks the
checker for the expression's type at each call.

When argument types differ, an ordinary signature uses their union, reduced
by the checker's subtype relation. For example, `int | bool` becomes `int`,
and a subclass disappears from a union containing its base class.

The return annotation must satisfy every caller. Towel chooses the narrower
declared return type, or a revealed type confirmed to be a subtype of every
relevant declaration. Thunks use `Callable[[], T]`. A class passed as a value
uses `type[C]`, rather than the constructor signature a checker may reveal.

A method's receiver is not annotated from its call sites: its defining class
already determines what bare `self` means. Once a helper has another
annotation, any remaining unspecified parts are completed with `Any`, so the
signature is not partial.

### Names and runtime behavior

Annotation names are private and chosen to avoid bindings already spelled
in the module. For example:

```python
if 0 > 1:
    from pkg.models import Item as _Item
```

An existing import can be reused when it is the name's only binding anywhere
in the module. New imports needed only by annotations use the immutable false
comparison `0 > 1`, which mypy and pyright still read for types. Existing
`TYPE_CHECKING` guards retain their runtime behavior; their mutable flags are
not a reason to add imports that could execute. Where needed, the generated
guard carries `# pragma: no cover` so coverage.py excludes it.
These choices avoid adding public names to star imports or rebinding the
program's names.

Generated annotations must also avoid introducing runtime effects. Compound
annotation forms are quoted where needed, including compatibility with the
project's oldest declared Python. For example, a project supporting Python
3.9 without postponed annotations cannot evaluate `int | None`, so that form
is written as a string. The [architecture reference](ARCHITECTURE.md#helper-annotations)
gives the exact rendering rules.

## Generic helpers and signature fallbacks

Towel preserves relationships between argument and result types when it can.
A shared helper for `list[int] -> int` and `list[str] -> str` can become
`list[T] -> T`. Integer and string addition can use a constrained type
parameter connecting both operands to the result.

Existing generic callers receive fresh helper type parameters with supported
bounds and constraints preserved. Instance and class methods retain type
parameters already bound by their host class; static helpers infer them from
their explicit arguments. See the
[type-parameter design](proposals/type-parameters.md) for the inference order
and unsupported cases.

Towel tries a precise ordinary signature, then supported generic signatures.
When those fail, it can try targeted `Any` substitutions and a broad `Any`
fallback. An unannotated variant is considered only when every remaining
error from the `Any` variant lies inside the helper itself; errors elsewhere
still reject it. Every candidate signature must pass the applicable checks.

Text identical to a file's current contents is withheld from mypy so its
incremental cache remains useful. Checker failure or remaining new errors
decline the proposal.

## Running without verification

`--no-types` preserves annotations already in the source and generates
unannotated helpers. It explicitly disables typed verification.

An API engine with no type oracle can copy caller declarations but cannot
reason about them: unions remain unreduced, and conflicting return declarations
leave the return annotation absent. This is distinct from proving a signature
with a checker.

For the full supported boundary, see
[Type annotations on helpers](KNOWN_LIMITATIONS.md#type-annotations-on-helpers).
