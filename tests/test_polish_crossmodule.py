# Copyright 2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Polish item 5: shared helper spacing and immediate generator iterable evaluation."""

import ast
from pathlib import Path

import pytest

from towel.formatting import formatter_for_project
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.argument_ownership import _supported_runtime
from tests.test_callback_parameters import helper_calls, refactored, run_script


@pytest.mark.parametrize("strict", [False, True])
def test_shared_host_helper_remains_black_formatted(tmp_path: Path, strict: bool) -> None:
    """Polish item 5: formatting a snippet must also leave two module-level blank lines."""
    (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
    package = tmp_path / "shop"
    package.mkdir()
    (package / "__init__.py").write_text("")
    host = package / "orders.py"
    borrower = package / "invoices.py"
    body = (
        "def {name}(amount):\n"
        "    subtotal = amount + {extra}\n"
        "    tax = subtotal * 8\n"
        "    shipping = subtotal // 100\n"
        "    return subtotal + tax + shipping\n"
    )
    host.write_text(body.format(name="order_total", extra=1))
    borrower.write_text("import shop.orders\n\n\n" + body.format(name="invoice_total", extra=2))
    formatter = formatter_for_project(host).tool
    assert formatter is not None
    for path in (host, borrower):
        path.write_text(formatter(path.read_text()) + "\n")
    engine = UnificationRefactorEngine(
        min_lines=3,
        skip_trivial_helpers=False,
        cross_module_helpers=True,
        snippet_formatter=formatter,
        strict=strict,
    )
    engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    generated = host.read_text()
    if strict and not _supported_runtime():
        assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0
        assert "extracted_func" not in generated
        return
    assert "def __extracted_func_" in generated
    assert "import __extracted_func_" in borrower.read_text()
    assert formatter(generated) == generated.rstrip("\n")


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("prefix", ["", "    marker = invoice.missing\n"])
def test_generator_outer_iterable_obeys_by_value_prefix(
    tmp_path: Path, strict: bool, prefix: str
) -> None:
    """Polish item 5: outer iterable is immediate, but a preceding failure keeps its thunk."""
    body = (
        "def {name}(invoice):\n"
        + prefix
        + "    values = (value * 2 for value in invoice.{field})\n"
        "    result = tuple(values)\n"
        "    return result\n"
    )
    source = (
        "class Invoice:\n"
        "    lines = [2, 3]\n"
        "    other = [4, 5]\n\n"
        + body.format(name="first", field="lines")
        + "\n"
        + body.format(name="second", field="other")
        + "\nfor function in (first, second):\n"
        "    try:\n"
        "        print(function(Invoice()))\n"
        "    except AttributeError as error:\n"
        "        print(error)\n"
    )
    generated, same = refactored(tmp_path, source, strict=strict)
    assert same
    if strict and not _supported_runtime():
        assert generated == source
        return
    calls = helper_calls(generated)
    assert calls
    deferred = any(
        isinstance(node, ast.Lambda)
        and isinstance(node.body, ast.Attribute)
        and node.body.attr == "lines"
        for node in ast.walk(ast.parse(generated))
    )
    assert deferred is (strict or bool(prefix)), generated


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("parameterize", [False, True])
def test_cross_module_generator_outer_iterable_is_immediate(
    tmp_path: Path, strict: bool, parameterize: bool
) -> None:
    """Polish item 5: a shared builtin sum does not delay its generator's outer iterable."""
    package = tmp_path / "shop"
    package.mkdir()
    (package / "__init__.py").write_text("")
    body = (
        "def {name}(invoice):\n"
        "    total = sum(value * 2 for value in invoice.{field})\n"
        "    tax = total * 8\n"
        "    shipping = total // 100\n"
        "    return total + tax + shipping\n"
    )
    host = package / "orders.py"
    borrower = package / "invoices.py"
    host.write_text(body.format(name="order_total", field="other"))
    borrower.write_text("import shop.orders\n\n" + body.format(name="invoice_total", field="lines"))
    driver = tmp_path / "run.py"
    driver.write_text(
        "from shop.orders import order_total\n"
        "from shop.invoices import invoice_total\n"
        "class Invoice:\n"
        "    lines = [2, 3]\n"
        "    other = [4, 5]\n"
        "for invoice in (Invoice(), None):\n"
        "    for function in (order_total, invoice_total):\n"
        "        try:\n"
        "            print(function(invoice))\n"
        "        except AttributeError as error:\n"
        "            print(error)\n"
    )
    before = run_script(driver)
    assert before[0] == 0 and "162" in before[1] and "90" in before[1]
    engine = UnificationRefactorEngine(
        min_lines=3,
        skip_trivial_helpers=False,
        cross_module_helpers=True,
        strict=strict,
        parameterize_builtins=parameterize,
    )
    engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    generated = borrower.read_text()
    assert run_script(driver) == before
    if strict and not _supported_runtime():
        assert "import __extracted_func_" not in generated
        assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0
        return
    if strict and not parameterize:
        assert "import __extracted_func_" not in generated
        assert engine.declined_pairs.get("builtin_may_differ_by_module", 0) > 0
        return
    assert "import __extracted_func_" in generated
    deferred = any(
        isinstance(node, ast.Lambda)
        and isinstance(node.body, ast.Attribute)
        and node.body.attr == "lines"
        for node in ast.walk(ast.parse(generated))
    )
    assert deferred is (strict or parameterize), host.read_text() + generated
