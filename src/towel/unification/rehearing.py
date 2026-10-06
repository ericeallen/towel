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

"""Exact refusal keys for an unchanged run, never a dependency approximation.

Installed checker tools and support packages must remain immutable during a run,
as they must for its baseline and final confirmation. Custom oracles and mypy
plugins can depend on state outside that contract and are never retained here.
Configuration bytes and oracle identity are rechecked at a rehearing. Unknown
candidate metadata declines caching rather than being omitted from the key.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import tomllib
import sys
import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional, Sequence, TypeAlias, Union, Tuple

from ..checker_project import _pyright_config_inputs
from ..diagnostics import Settings
from ..formatting import formatting_repeatability, _counterpart
from ..project_layout import find_project_root
from ..project_tools import python_tool_environment
from ..type_inference import (
    CombinedOracle,
    MypyInferrer,
    PyrightOracle,
    TypeOracle,
    _RelocatedOracle,
    _mypy_config,
    _source_groups,
)
from .annotation_wiring import _mypy_sections
from .block_comments import (
    Anchor,
    BlockComment,
    HelperComment,
    HelperComments,
    NodePath,
    Placement,
    ProtectedSpan,
    SiteComments,
    Surrounding,
)
from .models import ArgumentHandoff, RefactoringProposal, Replacement, ReusedFunction
from .splicing import BlockColumns

if TYPE_CHECKING:
    from .engine_state import EngineState

_RECORDS = frozenset(
    {
        ArgumentHandoff,
        RefactoringProposal,
        Replacement,
        ReusedFunction,
        BlockColumns,
        Anchor,
        BlockComment,
        HelperComment,
        HelperComments,
        NodePath,
        ProtectedSpan,
        SiteComments,
        Surrounding,
    }
)


class _UnknownKey(ValueError):
    """A value outside the complete, closed candidate schema."""


_KeyValue: TypeAlias = Union[None, bool, int, float, str, Tuple["_KeyValue", ...]]


def _project_many(kind: str, values: Iterable[object]) -> _KeyValue:
    return (kind, tuple(_project(value) for value in values))


def _serialized(value: _KeyValue) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _joined(kind: str, values: Iterable[object]) -> str:
    # Context boundaries serialize once; nested candidate structure stays typed.
    return _serialized(_project_many(kind, values))


def _project(value: object) -> _KeyValue:
    if value is None or (
        isinstance(value, (bool, int, float, str)) and type(value) in (bool, int, float, str)
    ):
        return (type(value).__name__, value)
    if type(value) in (tuple, list):
        assert isinstance(value, (tuple, list))
        return _project_many(type(value).__name__, value)
    if type(value) is frozenset:
        assert isinstance(value, frozenset)
        # Serialization is a sort key only; never embedded as an escaped string.
        items = sorted((_project(item) for item in value), key=_serialized)
        return ("frozenset", tuple(items))
    if type(value) is Placement:
        assert isinstance(value, Placement)
        return _project_many("Placement", (value.name, value.value))
    if isinstance(value, ast.AST) and type(value).__module__ == "ast":
        return _project_many(type(value).__name__, tuple(sorted(vars(value).items())))
    if type(value) in _RECORDS and dataclasses.is_dataclass(value):
        fields = dataclasses.fields(value)
        if hasattr(value, "__dict__") and set(vars(value)) - {field.name for field in fields}:
            raise _UnknownKey("record extension attributes")
        return _project_many(
            type(value).__name__,
            ((field.name, getattr(value, field.name)) for field in fields),
        )
    raise _UnknownKey(type(value).__name__)


def exact_candidate_key(proposal: RefactoringProposal) -> Optional[str]:
    """Every field/AST attribute in a linear-size key, or unsupported means no key."""
    try:
        return _serialized(_project(proposal))
    except (ValueError, TypeError, RecursionError):
        return None


def checker_context(oracle: Optional[TypeOracle], paths: Sequence[str]) -> Optional[str]:
    """The exact built-in checker identities and current configuration bytes.

    A private staged run controls its source changes; the directory revision
    records each application/stale-source recovery. No inference about which
    imports matter is used. Tool/support installations are immutable per run.
    """
    if type(oracle) is _RelocatedOracle:
        assert isinstance(oracle, _RelocatedOracle)
        inner = checker_context(oracle.inner, [oracle._original(path) for path in paths])
        return None if inner is None else _joined("relocated", (id(oracle), inner))
    if type(oracle) is CombinedOracle:
        assert isinstance(oracle, CombinedOracle)
        contexts = [checker_context(checker, paths) for checker in oracle.checkers]
        if not contexts or any(context is None for context in contexts):
            return None
        return _joined("combined", (id(oracle), *contexts))
    if type(oracle) not in (MypyInferrer, PyrightOracle):
        return None
    checker = "mypy" if type(oracle) is MypyInferrer else "pyright"
    try:
        roots = _source_groups({path: "" for path in paths}, checker)
        if not roots:
            return None
        files: set[Path] = set()
        for root in roots:
            files.update(
                root / name
                for name in (
                    "mypy.ini",
                    ".mypy.ini",
                    "setup.cfg",
                    "pyproject.toml",
                    "pyrightconfig.json",
                )
            )
            files.update(
                path
                for path in (root / ".github/workflows").glob("*")
                if path.suffix in {".yml", ".yaml"}
            )
            if checker == "mypy":
                config = _mypy_config(root)
                if config is not None:
                    options, overrides = _mypy_sections(Path(config))
                    if any(
                        "plugins" in section for section in (options, *(s for _, s in overrides))
                    ):
                        return None
            else:
                files.update(_pyright_config_inputs(root, representable=False))
        configuration = tuple(
            (
                str(path),
                (hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None),
            )
            for path in sorted(files)
        )
        return _joined(
            checker,
            (id(oracle), tuple(str(root) for root in sorted(roots)), configuration),
        )
    except (OSError, ValueError, TypeError, RecursionError):
        return None


_FORMAT_CONFIGURATION = (
    "pyproject.toml",
    "ruff.toml",
    ".ruff.toml",
    "setup.cfg",
    "tox.ini",
    ".flake8",
    ".isort.cfg",
    ".editorconfig",
    ".gitignore",
)


def _format_configuration_stamps(
    files: set[Path],
) -> tuple[tuple[str, Optional[str]], ...]:
    """Known local tool configuration plus Ruff's literal extends chain."""
    pending = list(files)
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        if path.suffix != ".toml" or not path.exists():
            continue
        data = tomllib.loads(path.read_text())
        if path.name == "pyproject.toml":
            tool = data.get("tool", {})
            data = tool.get("ruff", {}) if isinstance(tool, dict) else {}
        if not isinstance(data, dict):
            raise _UnknownKey("Ruff configuration is not an object")
        extend = data.get("extend")
        if extend is not None:
            if not isinstance(extend, str):
                raise _UnknownKey("Ruff extend is not a literal path")
            base = (path.parent / extend).resolve()
            if not base.is_file():
                raise _UnknownKey("Ruff base configuration is missing")
            files.add(base)
            pending.append(base)
    return tuple(
        (
            str(path),
            hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
        )
        for path in sorted(files)
    )


def renderer_context(callback: object, paths: Sequence[str]) -> Optional[str]:
    """Only registered built-ins; wrappers never certify arbitrary callbacks."""
    if callback is None:
        return "none"
    capability = formatting_repeatability(callback)
    if capability is None:
        return None
    try:
        roots = capability.roots or tuple(sorted({find_project_root(Path(path)) for path in paths}))
        configurations: set[Path] = set()
        for root in roots:
            directories = {root}
            for path in paths:
                current = _counterpart(root, Path(path)).parent
                if not current.is_relative_to(root):
                    return None
                while current.is_relative_to(root):
                    directories.add(current)
                    if current == root:
                        break
                    current = current.parent
            configurations.update(
                directory / name for directory in directories for name in _FORMAT_CONFIGURATION
            )
        return _joined(
            "renderer",
            (
                id(callback),
                capability.selection,
                _format_configuration_stamps(configurations),
            ),
        )
    except (OSError, ValueError, TypeError, RecursionError):
        return None


_EXECUTION_OPTIONS = (
    "annotate_helpers",
    "cross_module_helpers",
    "max_parameters",
    "min_lines",
    "parameterize_constants",
    "parameterize_builtins",
    "skip_trivial_helpers",
    "max_candidate_pairs",
    "incremental_global_passes",
    "excluded_directories",
)


def execution_context(engine: "EngineState") -> Optional[str]:
    """A built-in engine and registered repeatable rendering, or no caching.

    Installed tools/support packages and external VCS/system tool preferences
    are immutable during a run. Project tool configuration is re-stamped.
    The optimization never changes what a custom callback is asked to do.
    """
    from .refactor_engine import UnificationRefactorEngine

    if type(engine) is not UnificationRefactorEngine or type(engine._settings) is not Settings:
        return None
    snippet = renderer_context(engine.snippet_formatter, engine._analysis_paths)
    finishing = renderer_context(engine.file_finisher, engine._analysis_paths)
    if snippet is None or finishing is None:
        return None
    if any(
        name in vars(engine)
        for name in (
            "_infer_helper_annotations",
            "_annotation_ladder",
            "_attempt",
            "_materialize_refactoring",
        )
    ):
        return None
    context = checker_context(engine._type_run_oracle, engine._analysis_paths)
    if context is None:
        return None
    known = engine._type_known
    try:
        return _joined(
            "execution",
            (
                context,
                sys.executable,
                os.getcwd(),
                hashlib.sha256(
                    json.dumps(sorted(python_tool_environment().items())).encode()
                ).hexdigest(),
                snippet,
                finishing,
                tuple((name, getattr(engine, name)) for name in _EXECUTION_OPTIONS),
                tuple(
                    (field.name, getattr(engine._settings, field.name))
                    for field in dataclasses.fields(Settings)
                ),
                (
                    engine.unifier.max_parameters,
                    engine.unifier.parameterize_constants,
                    engine.unifier.promote_equal_hof_literals,
                ),
                tuple(sorted(engine._helper_name_counters.items())),
                tuple((error.path, error.message, error.line) for error in known.errors),
                tuple(sorted(known.moved)),
                tuple(
                    (path, hashlib.sha256(text.encode()).hexdigest())
                    for path, text in sorted(known.texts.items())
                ),
            ),
        )
    except (ValueError, TypeError, RecursionError):
        return None
