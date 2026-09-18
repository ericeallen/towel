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

"""The state and cross-cutting operations the engine's mixins rely on.

``UnificationRefactorEngine`` is assembled from mixins, one per
responsibility (placement, reuse, insertion, annotation wiring,
materialization, clustering, parallel evaluation, the fixed-point drivers).
Each mixin is a class in its own module and reaches the rest of the engine
only through the attributes and methods declared here, so a reader of one
module sees exactly what it depends on, and mypy checks that the engine
provides it. The engine's constructor assigns every attribute; the method
stubs are implemented by the engine or by another mixin.
"""

from __future__ import annotations

from typing import Dict, Tuple

from .semantic_safety import ImportGraphCache


class EngineState:
    """Attributes and operations shared across the engine's mixins (declarations only)."""

    _source_lines_cache: Dict[str, Tuple[Tuple[int, int], Tuple[str, ...]]]
    """Lines of files the apply path read, keyed by path, with the stat they were read at."""

    import_graph: ImportGraphCache
    """What this run has learned about the project's import graph."""
