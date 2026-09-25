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

"""What a differential case is: a small project, and the probes that observe it.

A case is data, independent of how it is run. The runner writes its files,
the observer executes its probes against the original and the refactored
program, and the exporter turns the same probes into the ``__main__`` block
of a hostile fixture, so every consumer reads one description.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Mapping, Optional, Tuple, Union

Layout = Literal["package", "single", "multi"]
"""``package``: ``pkg/m.py``. ``single``: a lone ``m.py`` refactored as a file.
``multi``: ``pkg/a.py`` and ``pkg/b.py`` sharing a star-imported ``pkg/common.py``."""

Checker = Literal["mypy", "pyright", "both"]
"""The type checker a typed case's ``pyproject.toml`` configures in strict mode."""


@dataclass(frozen=True)
class Calls:
    """Call ``target`` once for each argument tuple ``arguments`` evaluates to.

    Both are Python expressions evaluated in the probe namespace, where each
    of the case's modules is bound to its alias. ``target`` is evaluated
    once, so a bound method keeps its instance across the calls. Each call
    gets a deep copy of its arguments, and the copy is recorded after the
    call as well, so a mutation of an argument is part of what is compared.
    """

    target: str
    arguments: str


@dataclass(frozen=True)
class Value:
    """Evaluate ``expression`` and record its value."""

    expression: str


@dataclass(frozen=True)
class Statement:
    """Execute ``source`` for its effect on the program before the next probe."""

    source: str


Probe = Union[Calls, Value, Statement]


@dataclass(frozen=True)
class Case:
    """A generated project and how to observe it.

    ``files`` maps each path relative to the project root to its text.
    ``modules`` lists ``(alias, module name)`` in the order the observer
    imports them; the probes refer to the modules by alias. ``checker`` is
    set on a typed case only, whose project configures that checker.
    """

    name: str
    seed: int
    typed: bool
    layout: Layout
    features: frozenset[str]
    files: Tuple[Tuple[str, str], ...]
    modules: Tuple[Tuple[str, str], ...]
    probes: Tuple[Probe, ...]
    checker: Optional[Checker] = None

    @property
    def file_map(self) -> Mapping[str, str]:
        """``files`` as a mapping from relative path to text."""
        return dict(self.files)

    @property
    def refactored_file(self) -> Optional[str]:
        """The one file Towel is pointed at for a single-file case, None for a project."""
        return "m.py" if self.layout == "single" else None

    def probe_spec(self) -> Dict[str, object]:
        """The observer's input, as JSON-ready data."""
        return {
            "modules": [list(pair) for pair in self.modules],
            "probes": [_probe_json(probe) for probe in self.probes],
        }


def _probe_json(probe: Probe) -> Dict[str, str]:
    if isinstance(probe, Calls):
        return {"kind": "calls", "target": probe.target, "arguments": probe.arguments}
    if isinstance(probe, Value):
        return {"kind": "value", "expression": probe.expression}
    return {"kind": "statement", "source": probe.source}
