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

"""Before/after observations for the release audit's ordinary Python defects."""

from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_cli_integration import invoke


@pytest.mark.parametrize(
    "fixture",
    [
        "r1792_factory_finalizer_continuation",
        "r1792_aliased_suppress_conditional_read",
    ],
)
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("opaque_provider", [False, True])
def test_release_audit_behavior(
    tmp_path: Path, fixture: str, typed: bool, opaque_provider: bool
) -> None:
    """Both default checked publication and untyped publication preserve observations."""
    original = Path(__file__).parent / "hostile_cases" / f"{fixture}.py"
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    original_text = original.read_text()
    if opaque_provider:
        if fixture == "r1792_factory_finalizer_continuation":
            original_text = original_text.replace(
                "def acquire() -> Token:\n    return Token()",
                "class provider:\n"
                "    def obtain(self) -> Token:\n"
                "        return Token()\n"
                "factory = provider()",
            ).replace("token = acquire()", "token = factory.obtain()")
        else:
            original_text = original_text.replace(
                "from contextlib import suppress as absorb",
                "class absorb:\n"
                "    def __init__(self, exception: type[Exception]) -> None:\n"
                "        pass\n"
                "    def __enter__(self) -> None:\n"
                "        pass\n"
                "    def __exit__(self, *exc_info: object) -> bool:\n"
                "        return True\n",
            )
    source.write_text(original_text)
    before = subprocess.run([sys.executable, str(source)], capture_output=True, check=True)
    arguments = ["dry", str(source), str(output), "--no-interactive", "--progress", "none"]
    if not typed:
        arguments.append("--no-types")
    result = invoke(arguments)
    assert result.status == 0, result.stderr
    after = subprocess.run([sys.executable, str(output)], capture_output=True, check=True)
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    assert source.read_text() == original_text


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("alias", [False, True])
def test_multiple_finalizers_keep_their_cleanup_order(
    tmp_path: Path, typed: bool, alias: bool
) -> None:
    """An alphabetic tuple reorders destruction; aliases can delay it further."""
    fixture = Path(__file__).parent / "hostile_cases" / "r1792_factory_finalizer_order.py"
    original_text = fixture.read_text()
    if alias:
        original_text = original_text.replace(
            '    a = acquire("a")', '    a = acquire("a")\n    copy_z = z'
        )
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text(original_text)
    before = subprocess.run([sys.executable, str(source)], capture_output=True, check=True)
    arguments = ["dry", str(source), str(output), "--no-interactive", "--progress", "none"]
    if not typed:
        arguments.append("--no-types")
    result = invoke(arguments)
    assert result.status == 0, result.stderr
    assert "def __extracted_func_" in output.read_text()
    after = subprocess.run([sys.executable, str(output)], capture_output=True, check=True)
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    assert source.read_text() == original_text
