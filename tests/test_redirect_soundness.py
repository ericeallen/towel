# Copyright 2025-2026 Eric Allen
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

"""Redirect review: expression candidates must retain their own helper."""

from pathlib import Path

import pytest

from tests.test_callback_parameters import run_script
from tests.test_shape_review import dry

EXPRESSION = """(
        (value + 1) * (value + 2)
        + (value + 3) * (value + 4)
        + (value + 5) * (value + 6)
    )"""
PROVIDER = f"def total(value):\n    return {EXPRESSION}\n\n"
REPORT = f"def report(value):\n    found = {EXPRESSION}\n    return found\n\n"


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "clean",
        "comprehension_shadow",
        "comprehension_wrong_result",
        "comprehension_filter_shadow",
        "generator_shadow",
        "comprehension_later_binder",
        "setter_argument",
        "setter_default",
    ],
)
def test_expression_redirect_is_declined(tmp_path: Path, strict: bool, case: str) -> None:
    """Redirect review 1/2: every expression variant keeps independent name resolution."""
    cases = {
        "clean": REPORT + "print(report(2), total(3))\n",
        "comprehension_shadow": f"def report(values):\n    return [{EXPRESSION} for total, value in values]\n\nprint(report([(0, 2), (1, 3)]))\n",
        "comprehension_wrong_result": f"def report(values):\n    return [{EXPRESSION} for total, value in values]\n\nprint(report([(lambda value: -999, 2)]))\n",
        "comprehension_filter_shadow": f"def report(values):\n    return [value for total, value in values if {EXPRESSION}]\n\nprint(report([(0, 2), (1, 3)]))\n",
        "generator_shadow": f"def report(values):\n    return list({EXPRESSION} for total, value in values)\n\nprint(report([(0, 2), (1, 3)]))\n",
        "comprehension_later_binder": f"def report(values):\n    return [value for value in values if {EXPRESSION} for total in (0,)]\n\nprint(report([2, 3]))\n",
        "setter_argument": REPORT
        + 'def mutate(setter, module):\n    setter(module, "total", lambda value: -999)\nimport __main__ as mod\nmutate(setattr, mod)\nprint(report(2), total(2))\n',
        "setter_default": REPORT
        + 'def mutate(module, setter=setattr):\n    setter(module, "total", lambda value: -999)\nimport __main__ as mod\nmutate(mod)\nprint(report(2), total(2))\n',
    }
    source = tmp_path / "input.py"
    source.write_text(PROVIDER + cases[case])
    output = tmp_path / "output.py"
    before = run_script(source)
    assert before[0] == 0, before
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before
    generated = output.read_text()
    assert "def __extracted_func_" in generated
    # The clean control ensures disabling reuse does not disable extraction.
    assert "total(value)" not in generated.split("def report", 1)[1]
