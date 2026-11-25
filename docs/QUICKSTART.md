# Quick start

Three functions below compute a price the same way and differ only in their
discount. In about ten minutes Towel turns them into one helper and three
one-line callers; you name the helper, run the tests and keep the result. You
need macOS or Linux and a terminal.

## Install

```bash
uv tool install code-towel     # or: pipx install code-towel
towel --version
```

Without uv or pipx, make a virtual environment and keep it active while you
follow this page:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install "code-towel>=1.414"
```

The package is `code-towel` and the command is `towel`; the PyPI project named
`towel` is unrelated. Towel runs on Python 3.11 to 3.15.

## 1. Make a small project

In an empty directory:

```bash
mkdir demo
printf '[project]\nname = "demo"\nversion = "0.1.0"\n' > demo/pyproject.toml
```

Save this as `demo/charges.py`:

```python
def checkout_total(amount):
    base = max(amount, 0)
    discount = base * 0.10
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    return round(discounted + shipping + tax, 2)


def member_total(amount):
    base = max(amount, 0)
    discount = base * 0.25
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    return round(discounted + shipping + tax, 2)


def wholesale_total(amount):
    base = max(amount, 0)
    discount = base * 0.40
    discounted = base - discount
    tax = discounted * 0.08
    shipping = 5 if discounted < 100 else 0
    return round(discounted + shipping + tax, 2)
```

Save this as `demo/test_charges.py`:

```python
import unittest

from charges import checkout_total, member_total, wholesale_total


class PricingTests(unittest.TestCase):
    def test_prices(self):
        expected = {
            checkout_total: (5.0, 53.6, 194.4),
            member_total: (5.0, 45.5, 162.0),
            wholesale_total: (5.0, 37.4, 129.6),
        }
        for calculate, prices in expected.items():
            for amount, price in zip((0, 50, 200), prices):
                with self.subTest(function=calculate.__name__, amount=amount):
                    self.assertEqual(calculate(amount), price)
```

The tests pass:

```bash
python3 -m unittest discover -s demo
```

```text
.
----------------------------------------------------------------------
Ran 1 test in 0.000s

OK
```

## 2. Preview the change

`towel preview` shows what Towel would change, and changes nothing. This output
comes from an install with Black available; the plain install above prints a
different first line, explained below the diff:

```bash
towel preview demo
```

```diff
Note: formatting generated code with Black (all analyzed files already formatted).
Would apply 1 refactoring(s).
Analysed 2 Python files (4 functions).
Would change 1 file(s):
  charges.py: 1 refactoring(s)
    - Extract common code from checkout_total and member_total
--- before/charges.py
+++ after/charges.py
@@ -1,25 +1,19 @@
-def checkout_total(amount):
+def __extracted_func_0(amount, _param_0):
     base = max(amount, 0)
-    discount = base * 0.10
+    discount = base * _param_0
     discounted = base - discount
     tax = discounted * 0.08
     shipping = 5 if discounted < 100 else 0
     return round(discounted + shipping + tax, 2)


+def checkout_total(amount):
+    return __extracted_func_0(amount, 0.10)
+
+
 def member_total(amount):
-    base = max(amount, 0)
-    discount = base * 0.25
-    discounted = base - discount
-    tax = discounted * 0.08
-    shipping = 5 if discounted < 100 else 0
-    return round(discounted + shipping + tax, 2)
+    return __extracted_func_0(amount, 0.25)


 def wholesale_total(amount):
-    base = max(amount, 0)
-    discount = base * 0.40
-    discounted = base - discount
-    tax = discounted * 0.08
-    shipping = 5 if discounted < 100 else 0
-    return round(discounted + shipping + tax, 2)
+    return __extracted_func_0(amount, 0.40)
Input files were not changed.
```

The shared calculation became `__extracted_func_0`, and the discount, the one
thing that differed, became its parameter `_param_0`. Each of the three
functions now passes its own rate; the summary names the first two copies
Towel matched. Generated names are placeholders, `_param_0` here and
`__param_0` in some projects ([why](GENERATED_CODE.md#names-parameters-and-other-details)),
and step 4 replaces them. The first line says how the inserted code is laid out. This project
configures no formatter, but its files already match Black's defaults, so
Towel formats with Black when Black is installed where Towel runs (the
`format` extra installs it). With the plain install above it is not, and the
note says that generated code is inserted unformatted and names the
`code-towel[format]` extra.

## 3. Write the change to a copy

`towel dry` makes the change; despite its name, it writes (the name is for
"don't repeat yourself"). Given a new directory, it writes the refactored
project there and leaves `demo` as it was. It asks before writing; answer `y`:

```bash
towel dry demo cleaned
```

```text
...
1 refactoring(s) in 1 file(s): charges.py; will write cleaned/.
Write these changes? [y/N] y

Analysed 2 Python files (4 functions).
Applied 1 refactoring(s) across 1 file(s).
...
Wrote call-site before/after to cleaned/.towel-helpers.json (for naming; safe to delete).
```

Before the question, `dry` prints a banner and the same note as `preview`;
"fixed-point iteration" in the banner means it repeats until it finds nothing
more to extract.

The copy's tests pass too:

```bash
python3 -m unittest discover -s cleaned
```

```text
.
----------------------------------------------------------------------
Ran 1 test in 0.000s

OK
```

## 4. Name the helper

`rename-helpers --list` shows the helpers Towel wrote:

```bash
towel rename-helpers cleaned --list
```

```text
Found 1 extracted helper function(s):

  charges.py:1
    Function: __extracted_func_0
      def __extracted_func_0(amount, _param_0):
          base = max(amount, 0)
          discount = base * _param_0
      ...
```

Save this as `renames.json`, which names the helper and its parameter:

```json
{
  "__extracted_func_0": "discounted_total",
  "__extracted_func_0._param_0": "discount_rate"
}
```

Apply it, and run the tests again:

```bash
towel rename-helpers cleaned --rename-file renames.json
python3 -m unittest discover -s cleaned
```

```text
  __extracted_func_0 -> discounted_total
  __extracted_func_0._param_0 -> discount_rate

Applied 6 change(s)
.
----------------------------------------------------------------------
Ran 1 test in 0.000s

OK
```

Add `--preview` to see a rename's diff before applying it. Without
`--rename-file`, `rename-helpers` prints a prompt you can give an AI assistant,
which answers with a mapping like this one; Towel itself calls no AI service.
On a terminal it waits for the answer. Press Ctrl-C to cancel. With redirected
input it prints the prompt and exits. The [naming guide](NAMING.md) has the details.

## 5. Keep the result

Compare the original with the result:

```bash
diff -u demo/charges.py cleaned/charges.py
```

```diff
--- demo/charges.py	2026-11-17 10:00:00
+++ cleaned/charges.py	2026-11-17 10:02:00
@@ -1,25 +1,19 @@
-def checkout_total(amount):
+def discounted_total(amount, discount_rate):
     base = max(amount, 0)
-    discount = base * 0.10
+    discount = base * discount_rate
     discounted = base - discount
     tax = discounted * 0.08
     shipping = 5 if discounted < 100 else 0
     return round(discounted + shipping + tax, 2)


+def checkout_total(amount):
+    return discounted_total(amount, 0.10)
+
+
 def member_total(amount):
-    base = max(amount, 0)
-    discount = base * 0.25
-    discounted = base - discount
-    tax = discounted * 0.08
-    shipping = 5 if discounted < 100 else 0
-    return round(discounted + shipping + tax, 2)
+    return discounted_total(amount, 0.25)


 def wholesale_total(amount):
-    base = max(amount, 0)
-    discount = base * 0.40
-    discounted = base - discount
-    tax = discounted * 0.08
-    shipping = 5 if discounted < 100 else 0
-    return round(discounted + shipping + tax, 2)
+    return discounted_total(amount, 0.40)
```

Copy the reviewed file into the project, and check it there:

```bash
cp cleaned/charges.py demo/charges.py
python3 -m unittest discover -s demo
```

```text
.
----------------------------------------------------------------------
Ran 1 test in 0.000s

OK
```

That is the whole cycle: preview, write, test, name, review, keep.

## Next

On a real project you will usually work on a branch and let `dry` change the
files in place: `towel dry src/ src/`, then `git diff`.
[Using Towel on a project](USING_TOWEL.md) shows that workflow, and how to
limit and review a large run. When Towel leaves code alone, the summary says
why; [Why something was left alone](WHY_LEFT_ALONE.md) explains each reason.
If a helper's call has a `lambda` in it,
[Reading Towel's output](GENERATED_CODE.md) explains it.

See also the [command reference](CLI_GUIDE.md) and the
[Python API guide](USAGE_GUIDE.md).

[Documentation index](README.md)
