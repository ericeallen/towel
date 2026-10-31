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

"""File-local graph facts follow current content and parser diagnostics within a run."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Callable
import warnings

import pytest

from towel.unification.bounded_cache import memoization_disabled
from towel.unification.import_graph import (
    ImportGraphCache,
    ImportTimeCode,
    _has_import_time_effects,
    _import_edges,
    _module_level_import_bindings,
    _required_names,
)

_READERS = ("bindings", "effects", "required", "edges", "classes")


def _action(reader: str, path: Path, cache: ImportGraphCache) -> Callable[[], object]:
    host = path.with_name("host.py")
    program = cache.program_for(path)
    analyzer = ImportTimeCode(host.read_text(), path=host, cache=cache)
    actions: dict[str, Callable[[], object]] = {
        "bindings": lambda: _module_level_import_bindings(path, cache),
        "effects": lambda: _has_import_time_effects(path, cache),
        "required": lambda: _required_names(path, cache),
        "edges": lambda: _import_edges(path, program, cache),
        "classes": lambda: analyzer._quiet_imported_class("Base"),
    }
    return actions[reader]


def _package(tmp_path: Path, value: str) -> Path:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "host.py").write_text("from .base import Base\n")
    path = package / "base.py"
    path.write_text(f"import math\nvalue = {value}\nclass Base:\n    pass\n")
    return path


@pytest.mark.parametrize("reader", _READERS)
def test_warning_policy_changes_reach_warm_graph_readers(reader: str, tmp_path: Path) -> None:
    path = _package(tmp_path, "'\\q'")
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("ignore")
        action = _action(reader, path, ImportGraphCache())
        before = action()
        warnings.simplefilter("always")
        counts = []
        for _ in range(2):
            emitted.clear()
            assert action() == before
            counts.append(len(emitted))
        assert counts[0] > 0 and counts[0] == counts[1]
        warnings.simplefilter("error")
        warm = action()
        with memoization_disabled():
            fresh = action()
        assert warm == fresh
        assert warm != before
        warnings.simplefilter("ignore")
        assert action() == before


@pytest.mark.parametrize("reader", _READERS)
def test_integer_limit_changes_reach_warm_graph_readers(reader: str, tmp_path: Path) -> None:
    path = _package(tmp_path, "9" * 1000)
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(0)
        action = _action(reader, path, ImportGraphCache())
        before = action()
        sys.set_int_max_str_digits(640)
        warm = action()
        with memoization_disabled():
            fresh = action()
        assert warm == fresh
        assert warm != before
        sys.set_int_max_str_digits(0)
        assert action() == before
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("reader", _READERS)
def test_same_stat_source_replacement_reaches_warm_graph_readers(
    reader: str, tmp_path: Path
) -> None:
    path = _package(tmp_path, "1")
    action = _action(reader, path, ImportGraphCache())
    before = action()
    previous = path.stat()
    # The immutable program model still names the same files and imports. Only
    # this module's current local syntax changes; no new graph snapshot is needed.
    path.write_text(path.read_text().replace("value = 1", "value = ]"))
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert path.stat().st_size == previous.st_size
    assert path.stat().st_mtime_ns == previous.st_mtime_ns
    warm = action()
    with memoization_disabled():
        fresh = action()
    assert warm == fresh
    assert warm != before
