# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Protocol loading cannot execute a project tool through a mutable typing guard."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


def test_mutable_typing_guard_cannot_enable_a_parent_isort_import(tmp_path: Path) -> None:
    pytest.importorskip("isort")
    marker = tmp_path / "executed.txt"
    (tmp_path / "isort.py").write_text(
        'from pathlib import Path\nPath("executed.txt").write_text("project code ran")\n'
    )
    (tmp_path / "source.py").write_text("import sys\nimport os\n")
    script = """from pathlib import Path
import typing
from towel.project_tools import IsolatedFormatTool
typing.TYPE_CHECKING = True
tool = IsolatedFormatTool("isort")
try:
    root = Path.cwd()
    text = tool.render({"source": "import sys\\nimport os\\n", "root": str(root), "path": str(root / "source.py")})
    assert text.startswith("import os\\nimport sys\\n"), text
finally:
    tool.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
