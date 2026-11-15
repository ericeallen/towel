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

"""Audit R5: helpers follow the source's parameter order, receiver first."""

import ast
from pathlib import Path

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.extractor import HygienicExtractor
from towel.unification.substitution import Substitution


@pytest.mark.parametrize("receiver", ["self", "cls"])
def test_receiver_precedes_free_variables_and_generated_parameters(receiver: str) -> None:
    """Audit R5: sibling classes must not put the receiver last in a module helper."""
    block = ast.parse(f"return amount + {receiver}.offset + 12.50").body
    subst = Substitution()
    subst.add_mapping(0, ast.Constant(12.5), "__param_0")
    helper, order = HygienicExtractor().extract_function(
        template_block=block,
        substitution=subst,
        free_variables={receiver, "amount"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert tuple(order) == (receiver, "amount", "__param_0")
    assert tuple(a.arg for a in (*helper.args.posonlyargs, *helper.args.args)) == tuple(order)


def test_non_parameter_free_names_follow_first_use_not_alphabetical_order() -> None:
    """Audit R5: body order determines inputs that are not enclosing parameters."""
    _, order = HygienicExtractor().extract_function(
        template_block=ast.parse("return zebra + (beta * alpha)").body,
        substitution=Substitution(),
        free_variables={"alpha", "beta", "zebra"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert tuple(order) == ("zebra", "beta", "alpha")


def test_dictionary_inputs_follow_textual_order_across_keys_and_values() -> None:
    """Audit R5: AST field order groups dict keys before their intervening values."""
    _, order = HygienicExtractor().extract_function(
        template_block=ast.parse("return {zebra: beta, alpha: delta}").body,
        substitution=Substitution(),
        free_variables={"alpha", "beta", "delta", "zebra"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert tuple(order) == ("zebra", "beta", "alpha", "delta")


def test_generated_parameters_follow_first_use_even_if_discovered_later() -> None:
    """Audit R5: parameter numbers must not replace source order."""
    subst = Substitution()
    subst.add_mapping(0, ast.Constant(2), "__param_0")
    subst.add_mapping(0, ast.Constant(1), "__param_1")
    _, order = HygienicExtractor().extract_function(
        template_block=ast.parse("return 1 + 2").body,
        substitution=subst,
        free_variables=set(),
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert tuple(order) == ("__param_1", "__param_0")


@pytest.mark.parametrize("strict", [False, True])
def test_enclosing_signature_orders_pair_and_cluster_inputs(tmp_path: Path, strict: bool) -> None:
    """Audit R5: first site's declaration order survives pair and cluster extraction."""
    path = tmp_path / "source.py"
    path.write_text(
        "\n".join(
            f"def {name}(zebra, alpha, middle):\n"
            f"    total = alpha + middle + zebra\n"
            f"    total *= {scale}\n"
            f"    return total + zebra + alpha + middle\n"
            for name, scale in (("one", 2), ("two", 3), ("three", 4))
        )
    )
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    proposals = engine.analyze_file(str(path))
    assert proposals
    proposal = max(proposals, key=lambda p: len(p.replacements))
    assert len(proposal.replacements) == 3
    helper = proposal.extracted_function
    names = [a.arg for a in (*helper.args.posonlyargs, *helper.args.args)]
    assert [name for name in names if not name.startswith("__param_")] == [
        "zebra",
        "alpha",
        "middle",
    ]
