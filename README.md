# Towel

> “A towel ... is about the most massively useful thing an interstellar hitchhiker can have.”
>
> [Douglas Adams](https://www.goodreads.com/quotes/24779-a-towel-the-hitchhiker-s-guide-to-the-galaxy-says-is),
> [*The Hitchhiker's Guide to the Galaxy*](https://en.wikipedia.org/wiki/Towel_Day#Origin)

Towel finds repeated Python code and extracts it into shared helper functions.
It checks each extraction, preserves the parts that differ as parameters, and
leaves meaningful names for you to choose.

**Release status: 1.946 (beta).** Named for ln 7, approximately 1.945910149.
The
[1.946 overview](https://github.com/ericeallen/towel/blob/v1.946/docs/RELEASE_1_946.md)
and [changelog](https://github.com/ericeallen/towel/blob/v1.946/CHANGELOG.md#1946)
describe the changes: extraction of eligible branch-local outputs, precise
constant substitution, fewer unnecessary lambdas, stronger typing and lifetime
checks, and bounded reuse of source-analysis facts.

An earlier development audit at `9d57b0d` passed 10,583 tests and 45 subtests,
with 32 skips and 93.478% coverage. Its six consumer checks preserved their
original test outcomes and expected generated outputs. These results precede
the 1.946 metadata and documentation update; they do not certify the final
versioned artifacts. See the
[readiness report](https://github.com/ericeallen/towel/blob/v1.946/docs/PRODUCTION_READINESS.md)
for source-bound evidence and its limits. The latest
[local validation](https://github.com/ericeallen/towel/blob/v1.946/docs/PRODUCTION_READINESS.md#scoped-source-proof-memo-local-validation-october-18-2026)
records the full Python 3.11–3.13 runs, 93–94% coverage, byte-identical CLI
outputs and the remaining process-observer limitation.

The October 18 profile of `0b3494c` measured Rich 7.6% slower than 1.792
(97.4/97.6 versus 90.5/90.7 seconds, shared macOS host, Python 3.12).
Click's noisier difference includes a fifth extraction where 1.792 made four.
That profile predates scoped source-correspondence memoization, now implemented
for this release. It reuses a proof only for an owned immutable tree within one
analysis; caller trees and materialization retain fresh checks. No new timing
benefit is claimed. See
[where the remaining time goes](https://github.com/ericeallen/towel/blob/v1.946/docs/RELEASE_1_946.md#where-the-remaining-time-goes-profiled-on-0b3494c).

The earlier October 10 container cohorts use
versioned 1.946 source `a6dc6ed`: Voluptuous averaged 87.57 versus 90.98 seconds
(3.75% lower), while Click averaged 58.19 versus 47.16 seconds (23.39% slower).
These are two shared-host samples per source per workload; Rich has no actual
sample in that container campaign. Its full release gates and exact-commit CI
were incomplete; the October 18 native profile above is a separate measurement.
The [portable record](https://github.com/ericeallen/towel/blob/v1.946/tests/release_evidence/1.946-warning-guard/README.md)
binds all eight actual samples, identities, outputs and limits. These results
predate the subsequent backslash-parity repair. The latest prepared runtime
is not certified by those earlier source-bound cohorts.

The earlier October 10 shared-host comparison of `9d57b0d` with released 1.792
measured the complete cold CLI:
Click averaged 56.69 versus 46.53 seconds (21.85% slower), and Rich averaged
177.70 versus 99.77 seconds (78.10% slower), with two samples per version
per workload. The
[performance guide](https://github.com/ericeallen/towel/blob/v1.946/docs/PERFORMANCE.md)
records all samples, CPU time, workload differences and measurement limits.
README documentation links target the planned `v1.946` tag; their remote
destinations must be verified before publication.

**Start with the [Quick start](https://github.com/ericeallen/towel/blob/v1.946/docs/QUICKSTART.md),
or browse the [documentation index](https://github.com/ericeallen/towel/blob/v1.946/docs/README.md).**

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
def order_summary(order, label="Order"):
    items = [i for i in order.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return f"{label} {order.id}: ${total}"

def quote_summary(quote, label="Quote"):
    items = [i for i in quote.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return f"{label} {quote.id}: ${total}"
```

Towel proposes a helper and rewrites both callers:

```python
def __extracted_func_0(__param_0, label, _towel_owner):
    items = [i for i in __param_0.items if i.in_stock]
    subtotal = sum((i.price for i in items))
    total = round(subtotal * 1.08, 2)
    return f'{label} {__param_0.id}: ${total}'


def order_summary(order, label="Order"):
    _towel_arguments = [(label, order)]
    del order
    del label
    return __extracted_func_0(_towel_arguments[0][1], _towel_arguments[0][0], _towel_arguments.pop())

def quote_summary(quote, label="Quote"):
    _towel_arguments = [(label, quote)]
    del quote
    del label
    return __extracted_func_0(_towel_arguments[0][1], _towel_arguments[0][0], _towel_arguments.pop())
```

The argument box and final holder preserve the original argument cleanup
order when the entire body moves into a helper. The holder can be unread in
the helper: keeping those references alive is its purpose. Equal-looking class
arguments may instead be lookup suppliers, so a callback or another module can
change what each original read resolves. See
[argument evaluation](https://github.com/ericeallen/towel/blob/v1.946/docs/GENERATED_CODE.md#arguments-and-evaluation).
Partial extractions whose
owned values would cross frames without this proof are declined.

The shared structure becomes the helper's body; differing expressions become
parameters. Towel verifies that substituting each call's arguments into the
helper reproduces the block it replaces. It also checks binding, control flow
and evaluation order, declining transformations that fail those checks.

Generated names are deliberate placeholders. The
[naming guide](https://github.com/ericeallen/towel/blob/v1.946/docs/NAMING.md)
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

The [command-line guide](https://github.com/ericeallen/towel/blob/v1.946/docs/CLI_GUIDE.md)
covers in-place changes, exclusions, limits, diagnostics and recovery.

## Requirements

Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis uses the `fork` start method;
under other start methods analysis runs on a single core. Windows is not a
supported or validated release platform.

One core is enough; `TOWEL_WORKERS=1` limits analysis to one worker. Memory,
disk use and run time depend on the project and enabled tools. See
[requirements and resources](https://github.com/ericeallen/towel/blob/v1.946/docs/REQUIREMENTS.md)
and [performance measurements](https://github.com/ericeallen/towel/blob/v1.946/docs/PERFORMANCE.md)
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
[function opt-out guide](https://github.com/ericeallen/towel/blob/v1.946/docs/CLI_GUIDE.md#protect-a-function)
explains its placement and limits. Mark the frame whose identity matters,
including when its callee inspects it. The marker preserves that definition's
source, not module namespaces, absolute line numbers or unmarked call chains.
Review the
[known limitations](https://github.com/ericeallen/towel/blob/v1.946/docs/KNOWN_LIMITATIONS.md)
and run your tests before adopting changes.

In annotated projects, Towel uses the configured type checkers when available
and preserves caller-side narrowing. Existing checker errors are compared
against a baseline; a checker that cannot run refuses typed extraction.
The [typing guide](https://github.com/ericeallen/towel/blob/v1.946/docs/TYPING.md)
explains this policy and the unverified `--no-types` option.

## Documentation and development

The [documentation index](https://github.com/ericeallen/towel/blob/v1.946/docs/README.md)
groups tutorials, task guides, technical reference, design decisions and
release evidence. Contributors can start with
[development setup and checks](https://github.com/ericeallen/towel/blob/v1.946/CONTRIBUTING.md);
release maintainers should follow the
[release procedure](https://github.com/ericeallen/towel/blob/v1.946/docs/RELEASING.md).
Report security issues through the
[security policy](https://github.com/ericeallen/towel/blob/v1.946/SECURITY.md).

## License

Towel is licensed under the [Apache License 2.0](https://github.com/ericeallen/towel/blob/v1.946/LICENSE);
its source files carry Eric Allen copyright headers.
