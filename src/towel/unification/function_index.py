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

"""Lookups over one analysis's functions, built once and shared by every pair.

The pair stages ask the same questions of the analyzed functions for every
candidate pair: which functions a file holds, which of them carry a given
name, which one encloses a line range, whether any function in a file
declares a ``global``. Answering each by scanning the whole function list
made every pair cost the size of the project. The index answers them from
maps built once per analysis.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .models import FunctionArtifact, span_contains
from .visitors import body_without_docstring


@dataclass(frozen=True)
class FunctionIndex:
    """The functions of one analysis, grouped by file and by name.

    Every lookup preserves the order in which the analysis discovered the
    functions, so a caller that takes the first or last match sees the same
    one it would have found by scanning the list.
    """

    functions: Tuple[FunctionArtifact, ...]
    _by_path: Mapping[str, Tuple[FunctionArtifact, ...]]
    _by_name: Mapping[Tuple[str, str], Tuple[FunctionArtifact, ...]]
    _declares_global: Dict[str, bool] = field(default_factory=dict, compare=False)
    _source_digests: Dict[str, str] = field(default_factory=dict, compare=False)
    # Functions by (file, line of the first non-docstring statement): a site can
    # be the whole body only of a function whose body starts where the site does.
    _by_body_start: Mapping[Tuple[str, int], Tuple[FunctionArtifact, ...]] = field(
        default_factory=dict, compare=False
    )
    # Every clustered site of every pair asks for its function; the answer
    # for one (file, range) never changes within an analysis.
    _innermost: Dict[Tuple[str, Tuple[int, int]], Optional[FunctionArtifact]] = field(
        default_factory=dict, compare=False
    )

    @classmethod
    def build(cls, functions: Sequence[FunctionArtifact]) -> FunctionIndex:
        by_path: Dict[str, list[FunctionArtifact]] = {}
        by_name: Dict[Tuple[str, str], list[FunctionArtifact]] = {}
        by_body_start: Dict[Tuple[str, int], list[FunctionArtifact]] = {}
        for artifact in functions:
            by_path.setdefault(artifact.file_path, []).append(artifact)
            by_name.setdefault((artifact.file_path, artifact.node.name), []).append(artifact)
            body = body_without_docstring(artifact.node.body)
            if body:
                by_body_start.setdefault((artifact.file_path, body[0].lineno), []).append(artifact)
        return cls(
            tuple(functions),
            {path: tuple(entries) for path, entries in by_path.items()},
            {key: tuple(entries) for key, entries in by_name.items()},
            _by_body_start={key: tuple(entries) for key, entries in by_body_start.items()},
        )

    def body_starting_at(self, file_path: str, line: int) -> Tuple[FunctionArtifact, ...]:
        """Every function of ``file_path`` whose first non-docstring statement starts at ``line``."""
        return self._by_body_start.get((file_path, line), ())

    def in_file(self, file_path: str) -> Tuple[FunctionArtifact, ...]:
        """Every analyzed function of ``file_path``, in discovery order."""
        return self._by_path.get(file_path, ())

    def named(self, file_path: str, name: str) -> Tuple[FunctionArtifact, ...]:
        """Every function called ``name`` in ``file_path``, in discovery order."""
        return self._by_name.get((file_path, name), ())

    def innermost_at(
        self, file_path: str, line_range: Tuple[int, int]
    ) -> Optional[FunctionArtifact]:
        """The most deeply nested function of ``file_path`` whose span contains ``line_range``."""
        key = (file_path, line_range)
        if key in self._innermost:
            return self._innermost[key]
        enclosing = [
            artifact
            for artifact in self.in_file(file_path)
            if span_contains(artifact.node, line_range)
        ]
        found = max(enclosing, key=lambda artifact: artifact.node.lineno) if enclosing else None
        self._innermost[key] = found
        return found

    def declares_global(self, file_path: str) -> bool:
        """Whether any function of ``file_path`` carries a ``global`` statement."""
        known = self._declares_global.get(file_path)
        if known is None:
            known = any(
                isinstance(node, ast.Global)
                for artifact in self.in_file(file_path)
                for node in ast.walk(artifact.node)
            )
            self._declares_global[file_path] = known
        return known

    def source_digest(self, file_path: str) -> Optional[str]:
        """A digest of the source ``file_path`` was analyzed from, or None if unanalyzed."""
        digest = self._source_digests.get(file_path)
        if digest is None:
            functions = self.in_file(file_path)
            if not functions:
                return None
            digest = functions[0].module_digest
            self._source_digests[file_path] = digest
        return digest
