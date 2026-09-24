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

"""The package exposes the engine without loading it for the analysis modules."""

from __future__ import annotations

import subprocess
import sys

import pytest


def test_the_engine_is_reachable_from_the_package() -> None:
    from towel.unification import UnificationRefactorEngine
    from towel.unification.refactor_engine import UnificationRefactorEngine as direct

    assert UnificationRefactorEngine is direct


def test_other_attributes_are_still_errors() -> None:
    import towel.unification

    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        towel.unification.nope


def test_importing_an_analysis_module_leaves_the_engine_unloaded() -> None:
    probe = (
        "import sys\n"
        "import towel.unification.unifier\n"
        "import towel.unification.scope_analyzer\n"
        "print('towel.unification.refactor_engine' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
