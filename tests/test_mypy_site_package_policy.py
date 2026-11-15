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

"""Installed dependencies obey the project's mypy import search policy."""

from pathlib import Path
import sys

import pytest

pytest.importorskip("mypy")

from towel import _mypy_worker as worker  # noqa: E402
from towel.type_inference import CheckSuccess, MypyInferrer  # noqa: E402


@pytest.mark.parametrize("configuration", ["mypy.ini", "pyproject.toml"])
@pytest.mark.parametrize("no_site_packages", [False, True])
def test_no_site_packages_controls_installed_pytest_visibility(
    tmp_path: Path, configuration: str, no_site_packages: bool
) -> None:
    """Typed remediation item 1: honor the isolation that exposes unresolved pytest imports."""
    section = "[mypy]" if configuration == "mypy.ini" else "[tool.mypy]"
    config = tmp_path / configuration
    config.write_text(f"{section}\nno_site_packages = {str(no_site_packages).lower()}\n")
    path = tmp_path / "example.py"
    source = "import pytest\n"
    path.write_text(source)
    options = worker._options(tmp_path, str(config), str(tmp_path / "cache"), probe=False).options
    expected_executable = None if no_site_packages else sys.executable
    assert options.python_executable == expected_executable
    checker = MypyInferrer()
    try:
        result = checker.check(str(path), source)
    finally:
        checker.close()
    assert isinstance(result, CheckSuccess), result
    if no_site_packages:
        assert len(result.errors) == 1
        assert 'Cannot find implementation or library stub for module named "pytest"' in (
            result.errors[0].message
        )
    else:
        assert result.errors == ()
