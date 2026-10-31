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

"""A new import run must observe current files and their current locations."""

import ast
import os
from pathlib import Path

import pytest

from towel.unification.import_graph import ImportGraphCache, imported_definition_sites
from towel.unification.module_bindings import global_bindings
from towel.unification.protocol_bases import bases_allow_private_helpers


def test_new_run_resolves_changed_import_even_when_metadata_matches(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "first.py").write_text("class Base:\n    pass\n")
    (package / "other.py").write_text("from typing import Protocol as Base\n")
    host = package / "host.py"
    host.write_text("from .first import Base\n")
    cache = ImportGraphCache()
    assert imported_definition_sites(str(host), "Base", cache) == frozenset(
        {(package / "first.py", "Base")}
    )
    previous = host.stat()
    host.write_text("from .other import Base\n")
    os.utime(host, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    cache.begin_run()
    assert imported_definition_sites(str(host), "Base", cache) == frozenset(
        {(package / "other.py", "Base")}
    )


def test_relative_paths_cannot_hide_a_new_projects_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "from .base import Base\nclass Child(Base):\n    pass\n"
    for name, definition in (
        ("ordinary", "class Base:\n    pass\n"),
        ("protocol", "from typing import Protocol as Base\n"),
    ):
        package = tmp_path / name / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "base.py").write_text(definition)
        (package / "host.py").write_text(source)
    cache = ImportGraphCache()

    def allowed() -> bool:
        tree = ast.parse(source)
        klass = tree.body[1]
        assert isinstance(klass, ast.ClassDef)
        bindings = global_bindings(source)
        assert bindings is not None
        return bases_allow_private_helpers(klass, tree, bindings, "pkg/host.py", cache, ast.parse)

    monkeypatch.chdir(tmp_path / "ordinary")
    assert allowed()
    monkeypatch.chdir(tmp_path / "protocol")
    cache.begin_run()
    assert not allowed()


def test_relative_resolution_uses_the_callers_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    cache = ImportGraphCache()
    monkeypatch.chdir(first)
    assert cache.resolve("module.py") == first / "module.py"
    monkeypatch.chdir(second)
    assert cache.resolve("module.py") == second / "module.py"


def test_new_run_resolves_a_retargeted_symlink(tmp_path: Path) -> None:
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text("value = 1\n")
    second.write_text("value = 2\n")
    link = tmp_path / "current.py"
    link.symlink_to(first)
    cache = ImportGraphCache()
    assert cache.resolve(str(link)) == first
    link.unlink()
    link.symlink_to(second)
    cache.begin_run()
    assert cache.resolve(str(link)) == second
