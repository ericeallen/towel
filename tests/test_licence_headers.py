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

"""Every source file the sdist ships carries the licence header, as the package's modules do.

``scripts/add_copyright_headers.py`` is the policy: what it covers, and the
fixtures it leaves alone because a header would change what they test. A
new test or script without the header fails here; running the script adds
it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPOSITORY = Path(__file__).resolve().parents[1]


def _policy() -> ModuleType:
    location = REPOSITORY / "scripts" / "add_copyright_headers.py"
    spec = importlib.util.spec_from_file_location("add_copyright_headers", location)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_covered_file_carries_the_licence_header() -> None:
    policy = _policy()
    assert [str(path.relative_to(REPOSITORY)) for path in policy.missing(REPOSITORY)] == []


def test_the_policy_covers_the_tests_and_scripts_the_sdist_ships() -> None:
    covered = {path.relative_to(REPOSITORY).as_posix() for path in _policy().candidates(REPOSITORY)}
    assert "tests/conftest.py" in covered
    assert "tests/hostile_cases/h01_side_effect_order.py" in covered
    assert "scripts/verify-examples" in covered
    assert "scripts/check_mermaid.mjs" in covered


def test_each_fixture_left_alone_exists_and_says_why() -> None:
    policy = _policy()
    for relative, why in policy.LEFT_ALONE.items():
        assert (REPOSITORY / relative).is_file(), relative
        assert why, relative
