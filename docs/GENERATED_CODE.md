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
each caller can reach the helper. Cross-module sharing is opt-in.

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

By default, Towel declines pairs whose builtin bindings may differ between
sites, such as a local `len` at one site and the builtin at another, or a
module builtin that a test patches. `--parameterize-builtins` instead passes
each site's binding as an argument. A builtin resolved identically at every
site is still read directly. The option does not permit blocks that differ
in which builtin they use.

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

Generator and suspension operations, frame-sensitive calls such as `locals()`,
static local import cycles and cross-module global declarations are
conservatively rejected. Nested blocks that bind names needed outside the
block are rejected where full control-flow liveness is not supported.
Detected namespace rebinding, frame inspection and comprehension assignment
expressions also rule out affected candidates.

Every engine owns a bounded analysis session with content checks and isolated
AST snapshots. An engine instance and the test import-isolation harness each
require sequential use. See [the API guide](USAGE_GUIDE.md) for lifecycle rules.

These checks operate within the [documented model](KNOWN_LIMITATIONS.md).
Dynamic imports, opaque rebinding, arbitrary callbacks and external side
effects limit what can be established statically. Preview the result, inspect
the diff and run your own tests before adopting it.

## Decorators, instrumentation and reflection

Moving code out of a function can bypass a decorator that compiles or
instruments its body. Towel protects recognized body-transforming
instrumentation whether it is applied with `@`, an ordinary call such as
`fast = njit(kernel)`, stacked calls, or a recognized class hook.

Extraction requires decorators on the function and enclosing functions or
classes to be known to leave the body alone. Known cases include selected
standard-library, pytest and Click decorators, and project decorators whose
source shows that they only wrap or register the function. Unknown cases are
declined under the decorator's name. Cross-module rebinding can prevent a
binding from being treated as known.

A metaclass or lookup hook alone does not forbid class-private extraction.
Observations of an added helper, class-namespace scans, lookup logging and
stack depth fall under the explicit reflection limitation. This boundary does
not allow recognized body instrumentation to be bypassed.

Source-sensitive calls, such as inline-snapshot's `snapshot()`, stay where
they stand. An `assert` moves across modules only when pytest rewrites both
modules alike. The [reflection and instrumentation reference](KNOWN_LIMITATIONS.md#reflection-over-a-namespace-and-stack-depth)
explains these distinctions.
