# Towel

Towel finds repeated Python code and extracts it into shared helper functions.
Preview the changes, review the generated code, give the helpers meaningful
names, and run your project's tests before adopting the result.

**Release status: 1.949 (beta).** **Latest published release: 1.949.**
It was published on [PyPI](https://pypi.org/project/code-towel/1.949/) on
November 10, 2026, and its
[GitHub release](https://github.com/ericeallen/towel/releases/tag/v1.949)
followed on November 13. Check the
[GitHub releases](https://github.com/ericeallen/towel/releases) for completed
validation and publication records.

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

## Try it on your project

```bash
# See the proposed changes without modifying your project.
towel preview path/to/project

# Keep the original and write into a fresh sibling directory.
towel dry path/to/project path/to/cleaned --no-interactive

# Review the result before adopting it. diff exits 1 when it finds changes.
diff -ru path/to/project path/to/cleaned
```

The output path must not already exist or overlap the input. Helpers are shared
within a module by default; `--cross-module` also shares them between modules.
A complete preview runs the full transformation pipeline. `--quick` gives a
partial structural listing, which does not predict the final result.

Follow the [runnable quick start](https://github.com/ericeallen/towel/blob/v1.949/docs/QUICKSTART.md)
for a complete example through extraction, naming, tests and adoption. The
[naming guide](https://github.com/ericeallen/towel/blob/v1.949/docs/NAMING.md)
includes a manual rename mapping; no LLM account is required.

## What to expect

Towel checks source correspondence, bindings, control flow and evaluation order,
then declines transformations that fail those checks. An extraction must also
repay the complexity of its helper and callers. Some repeated code is better
left as it is.

Helpers initially have names such as `__extracted_func_0`. Preserving evaluation
and cleanup order can require lookup functions or argument-lifetime bookkeeping.
Review the whole change, including callers. The
[generated-code guide](https://github.com/ericeallen/towel/blob/v1.949/docs/GENERATED_CODE.md)
explains those cases; the
[command-line guide](https://github.com/ericeallen/towel/blob/v1.949/docs/CLI_GUIDE.md)
covers exclusions, bounded runs, output formats and recovery.

## Requirements

Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis uses the `fork` start method;
under other start methods analysis runs on a single core. Windows is not a
supported or validated release platform.

One core is enough; `TOWEL_WORKERS=1` limits analysis to one worker. Memory,
disk use and run time depend on the project and enabled tools. See
[requirements and resources](https://github.com/ericeallen/towel/blob/v1.949/docs/REQUIREMENTS.md)
and [performance measurements](https://github.com/ericeallen/towel/blob/v1.949/docs/PERFORMANCE.md)
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
[function opt-out guide](https://github.com/ericeallen/towel/blob/v1.949/docs/CLI_GUIDE.md#protect-a-function)
explains its placement and limits. Mark the frame whose identity matters,
including when its callee inspects it. The marker preserves that definition's
source, not module namespaces, absolute line numbers or unmarked call chains.
Review the
[known limitations](https://github.com/ericeallen/towel/blob/v1.949/docs/KNOWN_LIMITATIONS.md)
and run your tests before adopting changes.

In annotated projects, Towel uses the configured type checkers when available
and preserves caller-side narrowing. Existing checker errors are compared
against a baseline; a checker that cannot run refuses typed extraction.
The [typing guide](https://github.com/ericeallen/towel/blob/v1.949/docs/TYPING.md)
explains this policy and the unverified `--no-types` option.

## Documentation and development

The [documentation index](https://github.com/ericeallen/towel/blob/v1.949/docs/README.md)
groups tutorials, task guides, technical reference, design decisions and
release evidence. Contributors can start with
[development setup and checks](https://github.com/ericeallen/towel/blob/v1.949/CONTRIBUTING.md);
release maintainers should follow the
[release procedure](https://github.com/ericeallen/towel/blob/v1.949/docs/RELEASING.md).
Report security issues through the
[security policy](https://github.com/ericeallen/towel/blob/v1.949/SECURITY.md).

## License

Towel is licensed under the [Apache License 2.0](https://github.com/ericeallen/towel/blob/v1.949/LICENSE);
its source files carry Eric Allen copyright headers.

## Releases and validation

1.949 improves the product experience: complete-caller extraction selection,
concise preview output with versioned JSON results, time limits, and a
tutorial-first guide. Its
[published release](https://github.com/ericeallen/towel/releases/tag/v1.949)
carries the validated artifacts, and the repository's publication record
describes how they were validated and published.
1.948 repairs four silent `dry` miscompilations found in 1.947; its
[published release](https://github.com/ericeallen/towel/releases/tag/v1.948)
retains its validation record and artifacts under their original source
identity. Earlier releases and their evidence remain in the
[release history](https://github.com/ericeallen/towel/releases).
