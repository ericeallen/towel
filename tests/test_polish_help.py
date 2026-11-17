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

"""Polish item 4: exclusion help describes the actual source discovery policy."""

from pathlib import Path

import pytest

from tests.test_cli_integration import invoke
from towel.source_files import python_sources


@pytest.mark.parametrize("command", ["dry", "preview"])
def test_exclude_help_names_defaults_and_inclusion_options(command: str) -> None:
    """Polish 4: default build and Git exclusions and their remedies are discoverable."""
    result = invoke([command, "--help"])
    assert result.status == 0
    help_text = " ".join(result.stdout.split())
    for term in ("build", "dist", "*.egg-info", "site-packages", "Git-ignored"):
        assert term in help_text
    assert "--include-ignored" in help_text
    assert "target the file or directory directly" in help_text


@pytest.mark.parametrize("name", ["build", "dist", "demo.egg-info", "site-packages"])
def test_explicit_target_can_include_a_default_exclusion(tmp_path: Path, name: str) -> None:
    """Polish 4: verify the remedy stated by --exclude help on a small source tree."""
    directory = tmp_path / name
    directory.mkdir()
    source = directory / "example.py"
    source.write_text("answer = 42\n")
    assert python_sources(tmp_path) == []
    assert python_sources(directory) == [source]
