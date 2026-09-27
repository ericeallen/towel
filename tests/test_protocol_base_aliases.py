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

"""Direct Protocol aliases keep structural contracts; concrete subclasses keep method helpers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_helpers import refactor_to_fixed_point_silently
from tests.test_method_host_machinery import _module, _run
from towel.unification.models import ClassInfo
from towel.unification.refactor_engine import UnificationRefactorEngine


def _files(root: Path, files: dict[str, str]) -> None:
    (root / "pyproject.toml").write_text('[project]\nname="protocol-check"\nversion="0"\n')
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)


@pytest.mark.parametrize(
    "prelude, base, files",
    [
        ("from typing import Protocol as Contract", "Contract", {}),
        (
            "from aliases import Contract",
            "Contract",
            {"aliases.py": "from typing import Protocol as Contract\n"},
        ),
        (
            "import aliases as a",
            "a.Contract",
            {"aliases.py": "from typing import Protocol as Contract\n"},
        ),
        (
            "from aliases import Contract",
            "Contract",
            {
                "aliases.py": "from middle import Export as Contract\n",
                "middle.py": "import typing as t\nExport = t.Protocol\n",
            },
        ),
        (
            "from aliases import Contract",
            "Contract",
            {
                "aliases/__init__.py": "from .base import Export as Contract\n",
                "aliases/base.py": "from typing import Protocol\nExport: object = Protocol\n",
            },
        ),
        (
            "from aliases import Contract\nAlias = Contract",
            "Alias",
            {"aliases.py": "from typing_extensions import Protocol as Contract\n"},
        ),
        (
            "from aliases import Contract\nfrom typing import TypeVar\nT = TypeVar('T')",
            "Contract[T]",
            {"aliases.py": "from typing import Protocol as Contract\n"},
        ),
        (
            "from aliases import Contract",
            "Contract",
            {
                "aliases.py": "try:\n    from typing import Protocol as Contract\n"
                "except ImportError:\n    from typing_extensions import Protocol as Contract\n",
            },
        ),
        (
            "from aliases import *",
            "Contract",
            {"aliases.py": "from typing import Protocol as Contract\n__all__ = ['Contract']\n"},
        ),
        ("from typing import Protocol", "Protocol if True else object", {}),
        ("from typing import Protocol\nchoose = True", "Protocol if choose else object", {}),
        ("from typing import Protocol\nAlias = Protocol if True else object", "Alias", {}),
        ("from typing import Protocol\nbases = (Protocol,)", "*bases", {}),
        ("from typing import Protocol\ndef choose():\n    return Protocol", "choose()", {}),
        ("from typing import Protocol\n(Contract := Protocol)", "Contract", {}),
        ("from typing import Protocol\nfor Contract in (Protocol,):\n    pass", "Contract", {}),
        (
            "from typing import Protocol\nclass Aliases:\n    Contract = Protocol",
            "Aliases.Contract",
            {},
        ),
        (
            "from typing import Protocol\ndef protocol(cls):\n    return Protocol\n"
            "@protocol\nclass Contract:\n    pass",
            "Contract",
            {},
        ),
        (
            "from typing import Protocol\nContract = object\n"
            "def rebind():\n    global Contract\n    Contract = Protocol\nrebind()",
            "Contract",
            {},
        ),
    ],
)
def test_protocol_aliases_preserve_runtime_structural_conformance(
    tmp_path: Path, prelude: str, base: str, files: dict[str, str]
) -> None:
    source = _module(prelude, f"class Box({base}):\n    v: int\n")
    _files(tmp_path, {**files, "box.py": source})
    driver = (
        "from typing import runtime_checkable\nfrom box import Box\n"
        "class Concrete:\n    v = 1\n"
        "    def first(self, n):\n        return n\n"
        "    def second(self, n):\n        return n\n"
        "print(isinstance(Concrete(), runtime_checkable(Box)))\n"
    )
    before = _run(tmp_path, driver)
    assert before == "True\n|"
    final, applied = refactor_to_fixed_point_silently(str(tmp_path / "box.py"), min_lines=3)
    assert applied > 0
    assert "self.__extracted_func" not in final
    assert any(isinstance(node, ast.FunctionDef) for node in ast.parse(final).body)
    (tmp_path / "box.py").write_text(final)
    assert _run(tmp_path, driver) == before


@pytest.mark.parametrize(
    "prelude, base, files",
    [
        ("from typing import Protocol", "Protocol if False else object", {}),
        (
            "from aliases import Contract",
            "Contract",
            {"aliases.py": "from typing import Protocol\nclass Contract(Protocol):\n    v: int\n"},
        ),
        (
            "from middle import Implementation",
            "Implementation",
            {
                "middle.py": "from base import Contract\nclass Implementation(Contract):\n    pass\n",
                "base.py": "from typing import Protocol\nclass Contract(Protocol):\n    v: int\n",
            },
        ),
        ("from collections import UserDict", "UserDict", {}),
        (
            "from dataclasses import dataclass\n@dataclass\nclass Base:\n    pass",
            "Base",
            {},
        ),
    ],
)
def test_concrete_subclasses_keep_private_method_helpers(
    tmp_path: Path, prelude: str, base: str, files: dict[str, str]
) -> None:
    source = _module(prelude, f"class Box({base}):\n    v = 1\n")
    _files(tmp_path, {**files, "box.py": source})
    driver = "from box import Box\nprint(Box().first(1), Box().second(2))\n"
    before = _run(tmp_path, driver)
    assert before.endswith("4 6\n|")
    final, applied = refactor_to_fixed_point_silently(str(tmp_path / "box.py"), min_lines=3)
    assert applied > 0 and "self.__extracted_func" in final
    (tmp_path / "box.py").write_text(final)
    assert _run(tmp_path, driver) == before


@pytest.mark.parametrize("protocol", [False, True])
def test_alias_cycles_terminate_and_follow_every_possible_export(
    tmp_path: Path, protocol: bool
) -> None:
    source = _module("from aliases import Contract", "class Box(Contract):\n    v = 1\n")
    _files(
        tmp_path,
        {
            "box.py": source,
            "aliases.py": "from middle import Contract\n",
            "middle.py": "from aliases import Contract\n"
            + ("from typing import Protocol as Contract\n" if protocol else ""),
        },
    )
    engine = UnificationRefactorEngine(annotate_helpers=False)
    info = ClassInfo(name="Box", qualname="Box", file_path=str(tmp_path / "box.py"))
    # These deliberately unresolved cyclic modules need not be executable;
    # this pins the finite static query, not a claim of runtime equivalence.
    assert engine._can_host(info, {info.file_path: source}) is not protocol


def test_reexport_verdict_reads_changed_dependency_source(tmp_path: Path) -> None:
    source = _module("from aliases import Contract", "class Box(Contract):\n    v = 1\n")
    _files(tmp_path, {"box.py": source, "aliases.py": "class Contract:\n    pass\n"})
    engine = UnificationRefactorEngine(annotate_helpers=False)
    info = ClassInfo(name="Box", qualname="Box", file_path=str(tmp_path / "box.py"))
    tree = engine._parse_source(source)
    before = ast.dump(tree, include_attributes=True)
    assert engine._can_host(info, {info.file_path: source})
    (tmp_path / "aliases.py").write_text("from typing import Protocol as Contract\n")
    assert not engine._can_host(info, {info.file_path: source})
    assert engine._parse_source(source) is tree
    assert ast.dump(tree, include_attributes=True) == before
