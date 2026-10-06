# Towel

> “A towel ... is about the most massively useful thing an interstellar hitchhiker can have.”
>
> [Douglas Adams](https://www.goodreads.com/quotes/24779-a-towel-the-hitchhiker-s-guide-to-the-galaxy-says-is),
> [*The Hitchhiker's Guide to the Galaxy*](https://en.wikipedia.org/wiki/Towel_Day#Origin)

Towel finds repeated Python code and extracts it into shared helper functions.
It checks each extraction, preserves the parts that differ as parameters, and
leaves meaningful names for you to choose.

**Release status: 1.792 (beta).** Named for ln 6, approximately 1.791759469.
See the [changelog](https://github.com/ericeallen/towel/blob/v1.792/CHANGELOG.md#1792)
for this version's changes and the
[readiness report](https://github.com/ericeallen/towel/blob/v1.792/docs/PRODUCTION_READINESS.md)
for the validation scope.

On the controlled Packaging fixture, 1.792's complete typed, formatted command
is 11.9% slower than 1.772. The
[performance guide](https://github.com/ericeallen/towel/blob/v1.792/docs/PERFORMANCE.md#october-5-repaired-candidate-comparison)
records the workload, samples and limits; earlier development speedups do not
describe the final candidate.

**Start with the [Quick start](https://github.com/ericeallen/towel/blob/v1.792/docs/QUICKSTART.md),
or browse the [documentation index](https://github.com/ericeallen/towel/blob/v1.792/docs/README.md).**

## Install

The PyPI package is **[`code-towel`](https://pypi.org/project/code-towel/)**;
the command is **`towel`**. The PyPI name `towel` belongs to an unrelated project,
so `pip install towel` and `uvx towel` will not install this tool.

```bash
pip install code-towel
towel --version
towel --help
```

The runtime uses only the standard library. Optional tools format inserted
code and annotate and type-check generated helpers:

```bash
pip install "code-towel[format,types]"
```

Towel uses these tools when installed; `--no-format` and `--no-types` turn
either behavior off. Update with `pip install --upgrade code-towel`, or run
without installing with `uvx --from code-towel towel --help`.

## What it does

Given two functions that share a block:

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

Towel proposes a helper and rewrites both callers:

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

The shared structure becomes the helper's body; differing expressions become
parameters. Towel verifies that substituting each call's arguments into the
helper reproduces the block it replaces. It also checks binding, control flow
and evaluation order, declining transformations that fail those checks.

Generated names are deliberate placeholders. The
[naming guide](https://github.com/ericeallen/towel/blob/v1.792/docs/NAMING.md)
shows how to rename them as a checked batch, optionally with a coding assistant.

## Use

Preview, refactor into a copy, review the diff, and run your project's tests:

```bash
# Complete checked and formatted preview; input stays unchanged.
towel preview path/to/project

# Write the result into a new directory.
towel dry path/to/project path/to/cleaned --no-interactive

# Review before adopting the result.
diff -ru path/to/project path/to/cleaned
```

The output directory must not already exist or overlap the input. By default,
helpers are shared within one module. `--cross-module` also shares them across
modules, adding imports between them. For a faster partial structural listing,
use `towel preview path/to/project --quick`.

The [command-line guide](https://github.com/ericeallen/towel/blob/v1.792/docs/CLI_GUIDE.md)
covers in-place changes, exclusions, limits, diagnostics and recovery.

## Requirements

Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis uses the `fork` start method;
under other start methods analysis runs on a single core. Windows is not a
supported or validated release platform.

One core is enough; `TOWEL_WORKERS=1` limits analysis to one worker. Memory,
disk use and run time depend on the project and enabled tools. See
[requirements and resources](https://github.com/ericeallen/towel/blob/v1.792/docs/REQUIREMENTS.md)
and [performance measurements](https://github.com/ericeallen/towel/blob/v1.792/docs/PERFORMANCE.md)
for measured workloads and their conditions.

## Checks and limitations

Towel's soundness requirement is to preserve the behavior of programs that do
not use reflection or self-instrumentation, including through code they call.
An accepted transformation that changes such a program's behavior is a bug;
Towel must fix the transformation or refuse it.

Reflection and source, AST, bytecode, frame or namespace inspection are outside
its preservation guarantee, as is instrumentation that uses them to transform
functions or classes. Towel does not scan for these behaviors or refuse code to
protect them; decorators, ordinary calls and hooks have the same boundary.
Protect sensitive definitions with `# towel: no-extract` immediately after
the signature's final colon. The
[function opt-out guide](https://github.com/ericeallen/towel/blob/v1.792/docs/CLI_GUIDE.md#protect-a-function)
explains its placement and limits.
Review the
[known limitations](https://github.com/ericeallen/towel/blob/v1.792/docs/KNOWN_LIMITATIONS.md)
and run your tests before adopting changes.

In annotated projects, Towel uses the configured type checkers when available
and preserves caller-side narrowing. Existing checker errors are compared
against a baseline; a checker that cannot run refuses typed extraction.
The [typing guide](https://github.com/ericeallen/towel/blob/v1.792/docs/TYPING.md)
explains this policy and the unverified `--no-types` option.

## Documentation and development

The [documentation index](https://github.com/ericeallen/towel/blob/v1.792/docs/README.md)
groups tutorials, task guides, technical reference, design decisions and
release evidence. Contributors can start with
[development setup and checks](https://github.com/ericeallen/towel/blob/v1.792/CONTRIBUTING.md);
release maintainers should follow the
[release procedure](https://github.com/ericeallen/towel/blob/v1.792/docs/RELEASING.md).
Report security issues through the
[security policy](https://github.com/ericeallen/towel/blob/v1.792/SECURITY.md).

## License

Towel is licensed under the [Apache License 2.0](https://github.com/ericeallen/towel/blob/v1.792/LICENSE);
its source files carry Eric Allen copyright headers.
