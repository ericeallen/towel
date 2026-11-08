# Quick start

[Documentation index](README.md)

This example keeps the original project, extracts repeated pricing calculations
into a complete copy, names the helper, tests the result, and adopts one reviewed
file. It uses only Python's standard library and requires no assistant account.
Run the commands from a fresh working directory on macOS or Linux.

## Install

```bash
pip install code-towel
towel --version
```

The PyPI package is `code-towel` and the command is `towel`. Do not install
`towel`: that name belongs to a different, unrelated project. Python 3.11–3.13
are supported. These checkout documents describe development for the next
release; to try unreleased behavior, install this checkout with
`pip install -e .` instead of the published package.

## 1. Create and test a small project

```bash
mkdir demo
printf '[project]\nname = "towel-demo"\nversion = "0.0.0"\n' > demo/pyproject.toml
```

Save this as `demo/charges.py`:

```python
def checkout_total(amount):
    base = max(amount, 0)
    discount = base * 0.1
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    with_shipping = discounted + shipping
    total = with_shipping + tax
    return round(total, 2)


def quote_total(amount):
    base = max(amount, 0)
    discount = base * 0.1
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    with_shipping = discounted + shipping
    total = with_shipping + tax
    return round(total, 2)


def invoice_total(amount):
    base = max(amount, 0)
    discount = base * 0.1
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    with_shipping = discounted + shipping
    total = with_shipping + tax
    return round(total, 2)
```

Save this as `demo/test_charges.py`:

```python
import unittest

from charges import checkout_total, invoice_total, quote_total


class PricingTests(unittest.TestCase):
    def test_all_entry_points(self):
        for calculate in (checkout_total, invoice_total, quote_total):
            for amount, expected in ((0, 5.0), (50, 53.6), (150, 145.8)):
                with self.subTest(function=calculate.__name__, amount=amount):
                    self.assertEqual(calculate(amount), expected)
```

```bash
python -m unittest discover -s demo
```

## 2. Preview and extract into a copy

```bash
towel preview demo --no-types --no-format
towel dry demo cleaned --no-interactive --no-types --no-format
diff -u demo/charges.py cleaned/charges.py
```

`diff` exits 1 when it finds changes. The `cleaned` directory must not already
exist. The original `demo` is unchanged. This example disables optional tools
to make its result independent of installed formatters and type checkers.
For your own project, use the defaults or install
`pip install "code-towel[format,types]"` to enable those checks and formatting.

The repeated calculation moves into a helper such as `__extracted_func_0`.
Each caller retains bookkeeping that preserves argument cleanup order; its
purpose is explained in [generated code](GENERATED_CODE.md#arguments-and-evaluation).
Review those callers as well as the helper. Small duplicates whose new
interfaces cost more than they save can be left unchanged.

Complete preview runs the same pipeline as `dry` without publishing its output.
It shows a summary and final diff; `--inventory` additionally shows the helper
inventory. `--quick` is a partial structural listing, not a prediction of the
final result. See [CLI output formats](CLI_GUIDE.md#diagnostics).

## 3. Name the helper in the copied project

Because `cleaned` is a complete project with the same internal module names,
you can rename and test it before adopting anything into `demo`.

```bash
towel rename-helpers cleaned --list --json > helpers.json
```

Read the inventory. It includes the helper's source, callers, parameters and
exact `rename_key`. For this example, save the following as `renames.json`:

```json
{
  "charges.py:__extracted_func_0": "discounted_total_with_shipping"
}
```

Always use the key your own inventory reports; numbering can change when the
input changes. A mapping can also rename parameters. You can write it yourself
or ask an assistant to propose names; Towel calls no LLM service.

```bash
towel rename-helpers cleaned --complete-scope --rename-file renames.json --preview
towel rename-helpers cleaned --complete-scope --rename-file renames.json
python -m unittest discover -s cleaned
```

The rename batch aborts completely if a name is unsafe. `--complete-scope`
declares that this target contains every ordinary consumer, as this example
does. For a package copied out of a larger project, restore its original import
context first; see [consumer scope](NAMING.md#definition-selection-and-consumer-scope).

## 4. Review and adopt the result

```bash
diff -u demo/charges.py cleaned/charges.py
# After reviewing that one file and its passing tests:
cp cleaned/charges.py demo/charges.py
python -m unittest discover -s demo
```

The original project now contains the named, tested refactoring. In a real
repository, make this change on a branch, inspect every changed file, and use
its normal test and commit workflow. Copy only the files whose differences you
have reviewed. `.towel-helpers.json` is optional naming metadata; after naming,
it is not needed to run the refactored program.

## Use the workflow on your project

Start at the complete project root, write into a fresh sibling directory, and
keep the original until the copied project's own checks pass. By default,
helpers are shared only within one module. `--cross-module` also introduces
imports to share helpers between modules.

For bounded experiments, use `--time-limit 60`; expiration exits 124 and
publishes no result. `--max-refactorings N` intentionally stops after N changes.
`--max-pairs N` limits candidate pairs, not elapsed time or memory. For unattended
commands, always pass `--no-interactive`. Errors and progress go to stderr;
use `--json` for a structured result. See [compatibility](COMPATIBILITY.md).

- [Command-line guide](CLI_GUIDE.md) — options, exclusions and recovery
- [Naming guide](NAMING.md) — rename mappings and project context
- [Requirements](REQUIREMENTS.md) and [performance](PERFORMANCE.md) — support and resources
- [Known limitations](KNOWN_LIMITATIONS.md) — what is verified and outside the model
- [Python API guide](USAGE_GUIDE.md) — library integration
