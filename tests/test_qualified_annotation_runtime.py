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

"""An imported root does not make its attributes ready at a helper's definition.

Tornado's module-top helpers referred to tornado.websocket classes while
websocket itself was still importing. The generated annotations must remain
precise but inert; changing all existing annotations with a future import
would hide that defect by changing the input program's evaluation contract.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from tests.typed_fixtures import STRICT, apply_one, requires_mypy
from towel.unification.annotations import _joined_revealed, _joined_tuple, respell_bare


@pytest.mark.parametrize(
    "annotation, imports",
    [
        ("pkg.websocket.First", "import pkg.util"),
        ("pkg.websocket.First | pkg.websocket.Second", "import pkg.util"),
        ("list[pkg.websocket.First] | None", "import pkg.util"),
        ("typing.Any", "import typing\ntyping = object()"),
        ("typing.Optional[int]", "import typing"),
    ],
)
def test_qualified_annotations_stay_wholly_quoted(annotation: str, imports: str) -> None:
    """Binding the root alone proves neither attribute availability nor inertness."""
    helper = ast.parse(f"def helper(value: {annotation!r}) -> {annotation!r}: pass").body[0]
    assert isinstance(helper, ast.FunctionDef)
    before = ast.dump(helper, include_attributes=True)
    final = respell_bare(helper, ast.parse(imports), set())
    for node in (final.args.args[0].annotation, final.returns):
        assert isinstance(node, ast.Constant) and node.value == annotation
    assert ast.dump(helper, include_attributes=True) == before


def test_an_observed_any_result_does_not_unquote_its_qualified_alternative() -> None:
    """A late return union must retain both the checker result and runtime protection."""
    host = ast.parse("import pkg.util")
    result = _joined_revealed(["pkg.websocket.First", "Any"], host, True, preserve_any=True)
    assert isinstance(result, ast.Constant)
    assert result.value == "pkg.websocket.First | Any"
    ordinary = _joined_revealed(["int", "Any"], host, True, preserve_any=True)
    assert isinstance(ordinary, ast.Constant) and ordinary.value == "int | Any"


def test_tuple_composition_quotes_the_whole_type_after_joining_its_members() -> None:
    """A late tuple constructor must not undo each member's runtime protection."""
    result = _joined_tuple(
        ["pkg.websocket.First", "int", "Any", "int"],
        2,
        ast.parse("import pkg.util"),
        True,
    )
    assert isinstance(result, ast.Constant)
    assert result.value == "tuple[pkg.websocket.First | Any, int]"


@pytest.mark.parametrize("annotation", ["int", "Optional", "object"])
def test_simple_known_annotations_can_still_be_evaluated(annotation: str) -> None:
    helper = ast.parse(f"def helper(value: {annotation!r}) -> None: pass").body[0]
    assert isinstance(helper, ast.FunctionDef)
    final = respell_bare(helper, ast.parse("from typing import Optional"), set())
    written = final.args.args[0].annotation
    assert written is not None and not isinstance(written, ast.Constant)
    assert ast.unparse(written) == annotation


@pytest.mark.parametrize(
    "annotation, dependency, imported, value",
    [
        (
            "Model | None",
            "class Meta(type):\n"
            "    def __or__(cls, other):\n"
            "        events.append('union evaluated')\n"
            "        return cls\n"
            "class Model(metaclass=Meta): pass\n",
            "Model",
            "Model()",
        ),
        (
            "list[int]",
            "class list:\n"
            "    def __class_getitem__(cls, item):\n"
            "        events.append('subscription evaluated')\n"
            "        return cls\n",
            "list",
            "list()",
        ),
    ],
)
def test_resolved_names_do_not_prove_compound_annotation_inertness(
    tmp_path: Path, annotation: str, dependency: str, imported: str, value: str
) -> None:
    """A known name can expose overloads or shadow a supposedly builtin generic."""
    (tmp_path / "dependency.py").write_text("events = []\n" + dependency)
    prelude = f"from dependency import {imported}, events\n"
    original = f"def helper(value: {annotation!r}) -> object:\n    return value\n"
    helper = ast.parse(original).body[0]
    assert isinstance(helper, ast.FunctionDef)
    final = respell_bare(helper, ast.parse(prelude), set())
    driver = f"\nvalue = {value}\nassert helper(value) is value\nprint(events)\n"

    def run(function: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", "-c", prelude + function + driver],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    before = run(original)
    assert before.stdout == "[]\n"
    after = run(ast.unparse(final))
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    written = final.args.args[0].annotation
    assert isinstance(written, ast.Constant) and written.value == annotation
    # Resolving an atom does not invoke the class's overloaded operations.
    atom = ast.parse(f"def helper(value: {imported!r}) -> None: pass").body[0]
    assert isinstance(atom, ast.FunctionDef)
    atom_final = respell_bare(atom, ast.parse(prelude), set())
    assert isinstance(atom_final.args.args[0].annotation, ast.Name)


@requires_mypy
@pytest.mark.parametrize("annotated_locals", [False, True])
def test_a_composed_tuple_return_adds_no_runtime_subscription(
    tmp_path: Path, annotated_locals: bool
) -> None:
    """Exercise both declared-local and checker-revealed tuple return composition."""
    (tmp_path / "dependency.py").write_text(
        "events: list[str] = []\n"
        "class tuple:\n"
        "    def __class_getitem__(cls, item: object) -> object:\n"
        "        events.append('tuple subscription evaluated')\n"
        "        return cls\n"
    )
    source = """from typing import TYPE_CHECKING

if not TYPE_CHECKING:
    from dependency import tuple

class Box:
    def __init__(self) -> None:
        self.count = 1
        self.name = "n"

def first(box: Box) -> str:
    scaled = box.count * 2
    label = box.name.upper()
    print(scaled)
    return label + str(scaled)

def second(box: Box) -> str:
    scaled = box.count * 2
    label = box.name.upper()
    print(scaled)
    return label * scaled
"""
    if annotated_locals:
        source = source.replace("scaled =", "scaled: int =").replace("label =", "label: str =")
    path = tmp_path / "module.py"
    path.write_text(source)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import module, dependency\n"
                "print(module.first(module.Box()), module.second(module.Box()))\n"
                "print(dependency.events)",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    before = run()
    assert before.stdout == "2\n2\nN2 NN\n[]\n"
    outcome = apply_one(
        tmp_path,
        source,
        pick="first and second",
        config='[project]\nname = "m"\nrequires-python = ">=3.10"\n' + STRICT,
    )
    assert outcome.error is None, outcome.error
    assert outcome.module is not None
    path.write_text(outcome.module)
    after = run()
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    result = outcome.helper().returns
    assert isinstance(result, ast.Constant) and result.value == "tuple[str, int]"
    assert "Any" not in outcome.signature(), outcome.signature()
    assert outcome.module.count("label, scaled = ") == 2


@requires_mypy
def test_inferred_package_class_union_imports_and_preserves_existing_evaluation(
    tmp_path: Path,
) -> None:
    """Strict checking alone accepted the broken helper; import it in a fresh process too."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "events: list[str] = []\n"
        "def __getattr__(name: str) -> object:\n"
        "    events.append(name)\n"
        "    raise AttributeError(name)\n"
    )
    (package / "util.py").write_text("")
    source = """import pkg.util
from typing import Annotated

def mark() -> str:
    pkg.events.append("original annotation")
    return "original annotation"

def untouched(value: Annotated[int, mark()]) -> int:
    return value

class First:
    close_code: int | None = None
    close_reason: str | None = None

    def on_close(self, code: int | None, reason: str | None) -> None:
        self.close_code = code
        self.close_reason = reason
        self.on_connection_close()

    def on_connection_close(self) -> None:
        print("first", self.close_code, self.close_reason)

class Second:
    close_code: int | None = None
    close_reason: str | None = None

    def on_close(self, code: int | None, reason: str | None) -> None:
        self.close_code = code
        self.close_reason = reason
        self.on_connection_close()

    def on_connection_close(self) -> None:
        print("second", self.close_code, self.close_reason)
"""
    path = package / "websocket.py"
    path.write_text(source)
    driver = (
        "import pkg.websocket\n"
        "pkg.websocket.First().on_close(5, 'closed')\n"
        "pkg.websocket.Second().on_close(None, None)\n"
        "print(pkg.events, pkg.websocket.untouched(7))\n"
    )

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", "-c", driver],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    before = run()
    assert before.stdout == "first 5 closed\nsecond None None\n['original annotation'] 7\n"
    outcome = apply_one(
        tmp_path, source, pick="on_close and on_close", name="pkg/websocket.py", config=STRICT
    )
    assert outcome.error is None, outcome.error
    assert outcome.module is not None
    assert "Any" not in outcome.signature(), outcome.signature()
    helper = outcome.helper()
    receiver = next(arg.annotation for arg in helper.args.args if arg.arg == "self")
    assert receiver is not None
    spelling = receiver.value if isinstance(receiver, ast.Constant) else ast.unparse(receiver)
    assert spelling == "pkg.websocket.First | pkg.websocket.Second", outcome.signature()
    path.write_text(outcome.module)
    after = run()
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    assert isinstance(receiver, ast.Constant), outcome.signature()
    tree = ast.parse(outcome.module)
    assert not any(isinstance(n, ast.ImportFrom) and n.module == "__future__" for n in tree.body)
    original_untouched = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "untouched"
    )
    final_untouched = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "untouched"
    )
    assert ast.dump(final_untouched) == ast.dump(original_untouched)
