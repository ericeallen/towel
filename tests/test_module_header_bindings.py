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

"""Definition headers bind their enclosing scope; callable bodies do not."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_definition_time_builtins import _source
from tests.test_parameterize_builtins import _refactor, _run, _write
from towel.unification.module_bindings import global_bindings

MAPPING = '(__builtins__ := {"len": lambda data: 100})'
HEADERS = (
    f"def configure(value={MAPPING}):\n    pass\n",
    f"def configure(*, value={MAPPING}):\n    pass\n",
    f"async def configure(value={MAPPING}):\n    pass\n",
    f"unused = lambda value={MAPPING}: None\n",
    f"def configure(value=(lambda item={MAPPING}: None)):\n    pass\n",
)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    ("position", "expected"),
    [("before", "306 306\n"), ("between", "9 306\n"), ("after", "9 9\n")],
)
@pytest.mark.parametrize(
    "header",
    HEADERS,
    ids=["positional", "keyword-only", "async", "lambda", "nested-lambda"],
)
def test_header_replacements_preserve_captured_builtin_mappings(
    tmp_path: Path, flag: bool, position: str, expected: str, header: str
) -> None:
    source = _source(**{position: header})
    _write(tmp_path, {"m.py": source})
    assert _run(tmp_path, "m.py") == expected
    applied, declined = _refactor(tmp_path / "m.py", flag)
    assert _run(tmp_path, "m.py") == expected
    assert applied == 0
    assert "builtin_mapping_replaced" in declined
    assert (tmp_path / "m.py").read_text() == source
    bindings = global_bindings(source)
    assert bindings is not None and "__builtins__" in bindings.bindings


@pytest.mark.parametrize(
    "header",
    [
        f"def configure(value: {MAPPING}, /):\n    pass\n",
        f"def configure(value: {MAPPING}):\n    pass\n",
        f"def configure(*values: {MAPPING}):\n    pass\n",
        f"def configure(*, value: {MAPPING}):\n    pass\n",
        f"def configure(**values: {MAPPING}):\n    pass\n",
        f"def configure() -> {MAPPING}:\n    pass\n",
    ],
    ids=["positional-only", "positional", "vararg", "keyword-only", "kwarg", "return"],
)
def test_eager_annotations_record_module_bindings(tmp_path: Path, header: str) -> None:
    source = _source(before=header)
    # The test module postpones its own annotations; these source modules do not.
    compile(source, "m.py", "exec", dont_inherit=True)
    _write(tmp_path, {"m.py": source})
    assert _run(tmp_path, "m.py") == "306 306\n"
    bindings = global_bindings(source)
    assert bindings is not None and "__builtins__" in bindings.bindings


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    "header",
    [
        f"unused = lambda: {MAPPING}\n",
        f"unused = lambda: (lambda value={MAPPING}: None)\n",
        f"class Holder:\n    def method(value={MAPPING}):\n        pass\n",
        (
            "def local_context():\n"
            f"    local = lambda value={MAPPING}: None\n"
            "    return local\n"
            "local_context()\n"
        ),
    ],
    ids=["uncalled-body", "nested-uncalled-body", "class-namespace", "function-local"],
)
def test_local_or_unexecuted_bindings_keep_safe_extractions(
    tmp_path: Path, flag: bool, header: str
) -> None:
    source = _source(before=header)
    _write(tmp_path, {"m.py": source})
    assert _run(tmp_path, "m.py") == "9 9\n"
    bindings = global_bindings(source)
    assert bindings is not None
    assert "__builtins__" not in bindings.bindings
    assert "__builtins__" not in bindings.rebound_by_global
    applied, declined = _refactor(tmp_path / "m.py", flag)
    assert applied == 1
    assert "builtin_mapping_replaced" not in declined
    assert _run(tmp_path, "m.py") == "9 9\n"
