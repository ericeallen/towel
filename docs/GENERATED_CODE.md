# Understanding generated code

[Documentation index](README.md)

This guide explains the shape of extracted helpers and the checks behind them.
For precise supported boundaries, see [Known limitations](KNOWN_LIMITATIONS.md).

On this page:

- [Extracting a shared helper](#extracting-a-shared-helper)
- [Comments and formatting](#comments-and-formatting)
- [Where helpers live](#where-helpers-live)
- [Arguments and evaluation](#arguments-and-evaluation)
- [Why some arguments are wrapped in lambda](#why-some-arguments-are-wrapped-in-lambda)
- [What verification establishes](#what-verification-establishes)
- [Decorators, instrumentation and reflection](#decorators-instrumentation-and-reflection)

## Extracting a shared helper

Towel uses *anti-unification*, following the work of
[Reynolds and Plotkin](ARCHITECTURE.md#references). It computes the
least-general generalization of matching blocks: their shared structure
becomes the helper body, and differences become parameters.

For example:

```python
def order_summary(order):
    items = [i for i in order.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return f"Order {order.id}: ${total}"

def quote_summary(quote):
    items = [i for i in quote.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return f"Quote {quote.id}: ${total}"
```

The repeated block becomes a helper with a placeholder name:

```python
def __extracted_func_0(__param_0):
    items = [i for i in __param_0.items if i.in_stock]
    subtotal = sum((i.price for i in items))
    total = round(subtotal * 1.08, 2)
    return total

def order_summary(order):
    total = __extracted_func_0(order)
    return f"Order {order.id}: ${total}"

def quote_summary(quote):
    total = __extracted_func_0(quote)
    return f"Quote {quote.id}: ${total}"
```

Each caller keeps its own result formatting and receives the value it needs
from the helper. Towel skips trivial extractions that would add indirection
without sharing real logic. After reviewing the result, use the
[naming workflow](NAMING.md) to replace placeholders.

## Comments and formatting

Comments attached to moved code travel with it, including `# type: ignore`
and `# pragma: no cover`. Blocks with differing tool directives are left alone:
a shared line cannot retain two different directives.

A block is also left alone when a region directive opened outside it covers
it, such as `# fmt: off`, `# ruff: disable`, `# isort: off` or
`# pylint: disable`.

With the optional formatting tools installed, inserted code follows the
project's formatter configuration: `ruff format` when the project configures
ruff, otherwise Black, using the declared line length. Added imports are
sorted with ruff's import rules or isort when the project uses them. Install
these tools with `pip install "code-towel[format]"`; `--no-format` disables this
step. Each tool respects the files its configuration excludes.

Sorting must not change the order of existing imports, because that order can
affect execution. If the sorter would reorder the file's existing imports,
Towel skips import sorting for that file and reports it.

Helper annotations are described in the [typing guide](TYPING.md).

## Where helpers live

A block shared by methods of one class can become a class-private helper,
called as `self.__extracted_func_0(...)`. Name mangling protects it from
accidental overrides by ordinary differently named subclasses. Towel adds
such a method only to the class that already held the duplicated code.

A block shared across classes becomes a module-level helper taking the
receiver as an argument. You decide during review whether a different class
design would be better.

If a duplicate is an existing function's entire body, that function calls the
new helper like every other copy. Other callers are not redirected to the
existing function: patching or rebinding that function must not change their
behavior. Compatible helpers generated during the current run can be shared;
this does not make input functions interchangeable.

Across modules, [host and import checks](CROSS_MODULE.md) must establish that
each caller can reach the helper without advancing a module load or replacing
an ordinary parent-package binding. Cross-module sharing is opt-in. A fresh
shared helper appears before ordinary imports and effects, after the header,
docstring and future directives. Signature annotations that need bindings are
quoted so the definition needs no later bindings and a partially initialized
host can supply it. Reusing a generated helper requires its existing definition
to be available before operations that could reenter its module.

New imports needed only for annotations use `if 0 > 1:`. This immutable false
comparison never runs its body, while mypy and pyright still read the imported
types. Existing mutable `TYPE_CHECKING` guards keep their original behavior.

## Arguments and evaluation

Differing subexpressions become parameters, but moving an expression to a
call's argument list can change when or how often it runs. Towel chooses the
argument form accordingly:

- Literals, names definitely available at the call site, and tuples of those
  can be passed eagerly.
- A name that may not be available on every path is deferred in a thunk.
- Other expressions are deferred so their original evaluation order, count
  and conditions are retained.
- Expressions using names bound inside the extracted block are lambda-lifted:
  the deferred function receives those names as arguments.

When a same-module helper and all its callers resolve a name at module scope,
the helper can read it directly. It need not receive a module-level class,
import or later-defined function as a parameter. Cross-file helpers still
parameterize such names.

By default, a cross-file helper cannot read bare builtins from its host on
another caller's behalf, apart from the interpreter constant `__debug__`.
`--parameterize-builtins` explicitly permits caller-side lookup thunks such as
`lambda: len`; each original use then reads the caller's current binding.
Only the existing first-effect, single-use proof permits an eager argument.
Same-module builtin reads stay bare. The option does not permit blocks that
differ in which builtin they use.

## Why some arguments are wrapped in lambda

A zero-argument lambda is a *thunk*: it delays an expression until the helper
calls it. For example:

```python
notify(lambda: welcome_email(user))
```

Consider an original block that sends only to active users:

```python
if user.active:
    send(welcome_email(user))
```

Passing the expression directly would change its behavior:

```python
# WRONG: welcome_email(user) runs even when user.active is False.
def notify(message, user):
    if user.active:
        send(message)

notify(welcome_email(user), user)
```

The deferred form preserves the condition:

```python
def notify(build_message, user):
    if user.active:
        send(build_message())

notify(lambda: welcome_email(user), user)
```

The same principle applies inside loops and to expressions that may raise.
A thunk may run repeatedly or not at all, exactly where the original expression
stood. Towel removes the wrapper only when the helper would evaluate it first,
once and unconditionally, making eager evaluation equivalent.

## What verification establishes

For each proposed extraction, Towel substitutes each call's arguments back
into the helper. The result must reproduce the original block up to renamed
binders. Separate checks cover name binding, control flow and evaluation order.
The original Python operators are preserved.

Generator and suspension operations, static local import cycles and
cross-module global declarations are
conservatively rejected. Nested blocks that bind names needed outside the
block are rejected where full control-flow liveness is not supported.
Lexical binding conflicts and comprehension assignment expressions also rule
out affected candidates.

Every engine owns a bounded analysis session with content checks and isolated
AST snapshots. An engine instance and the test import-isolation harness each
require sequential use. See [the API guide](USAGE_GUIDE.md) for lifecycle rules.

These checks must preserve the behavior of programs that do not use reflection
or self-instrumentation, including through code they call. An accepted change
to ordinary binding, control flow, evaluation order or effects is a defect to
fix or refuse. The [documented model](KNOWN_LIMITATIONS.md) describes the
checks and their conservative refusals. Preview the result, inspect the diff
and run your own tests before adopting it.

## Decorators, instrumentation and reflection

Moving code into a helper changes the source and structure seen by reflection
and instrumentation. Towel offers no preservation guarantee for source, AST,
bytecode, frame or namespace inspection, or for instrumentation that compiles,
rewrites or wraps functions and classes using that information. This applies
uniformly to explicit decorators, ordinary calls, import hooks and class hooks.

There is no body-instrumentation allowlist or special refusal protecting these
behaviors. Code under typeguard or numba, inline-snapshot calls and assertions
subject to pytest rewriting may move. The resulting instrumentation, source
observations or diagnostic text may differ. Ordinary nonreflective decorator
behavior, receiver semantics and effects remain within the usual checks.

A private helper avoids accidental overrides; it does not hide a new name from
namespace scans or preserve lookup logging or stack depth. See the
[reflection and instrumentation reference](KNOWN_LIMITATIONS.md#reflection-over-a-namespace-and-stack-depth)
and the [October 2 decision](DECISIONS.md#2026-10-02-reflection-and-self-instrumentation-are-outside-the-preservation-contract).
