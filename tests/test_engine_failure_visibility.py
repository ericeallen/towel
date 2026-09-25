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

"""Unexpected compiler failures cannot masquerade as no refactoring opportunities."""

from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize(
    "component,method",
    [
        ("unifier", "unify_blocks"),
        ("extractor", "extract_function"),
        ("extractor", "generate_call"),
    ],
)
def test_unexpected_engine_error_propagates(tmp_path: Path, component: str, method: str) -> None:
    source = tmp_path / "example.py"
    source.write_text(
        "def first(x):\n    y=x+1\n    z=y*2\n    return z\n\ndef second(x):\n    y=x+1\n    z=y*2\n    return z\n"
    )
    assert UnificationRefactorEngine(min_lines=3).analyze_files([str(source)], progress="none")
    # A second engine, so no structural cache from the first run can serve
    # the answer without calling the component.
    engine = UnificationRefactorEngine(min_lines=3)
    with patch.object(
        getattr(engine, component), method, side_effect=RuntimeError("invariant failed")
    ):
        with pytest.raises(RuntimeError, match="invariant failed"):
            engine.analyze_files([str(source)], progress="none")
    # The failure leaves the engine usable: the next analysis succeeds.
    assert engine.analyze_files([str(source)], progress="none", invalidate_paths=[str(source)])
