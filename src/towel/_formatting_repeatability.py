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

"""Explicit repeatability for built-in formatting, never inferred from arbitrary callbacks.

Only built-in factories register. Checked/sorting wrappers propagate registered
input capabilities. A sorter returning a degraded result invalidates its token
and every registered wrapper derived from it. Weak identities never keep a
callback or its owned subprocess alive. Tools/support installations are fixed
for one run; consumers must additionally bind project configuration bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import types
import weakref
from typing import Callable, Optional, Tuple, TypeVar

from .project_tools import IsolatedFormatTool


class _RepeatabilityToken:
    __slots__ = ("__weakref__",)


_INVALID_REPEATABILITY: weakref.WeakSet[_RepeatabilityToken] = weakref.WeakSet()


@dataclass(frozen=True)
class FormattingRepeatability:
    """A built-in callback's immutable tool/options/configuration selection.

    This capability is private to built-in factories. Wrapping an arbitrary
    callback does not certify it. Installed tools and support packages remain
    fixed during one run. Configurations are re-stamped by the rehearing driver.
    """

    selection: Tuple[str, ...]
    roots: Tuple[Path, ...] = ()
    workers: Tuple[weakref.ReferenceType[IsolatedFormatTool], ...] = ()
    tokens: Tuple[_RepeatabilityToken, ...] = field(
        default_factory=lambda: (_RepeatabilityToken(),)
    )


_REPEATABLE_FORMATTING: weakref.WeakKeyDictionary[object, FormattingRepeatability] = (
    weakref.WeakKeyDictionary()
)
_Callback = TypeVar("_Callback", bound=Callable[..., object])


def _register_repeatable(callback: _Callback, context: FormattingRepeatability) -> _Callback:
    if isinstance(callback, types.FunctionType):
        _REPEATABLE_FORMATTING[callback] = context
    return callback


def formatting_repeatability(callback: object) -> Optional[FormattingRepeatability]:
    """A registered built-in function with live owned tools; custom callbacks get none."""
    if not isinstance(callback, types.FunctionType):
        return None
    context = _REPEATABLE_FORMATTING.get(callback)
    if context is None or any(token in _INVALID_REPEATABILITY for token in context.tokens):
        return None
    for reference in context.workers:
        tool = reference()
        if (
            tool is None
            or tool._closed
            or tool._owner != os.getpid()
            or tool._process.poll() is not None
        ):
            return None
    return context


def _propagate_repeatability(callback: _Callback, *inputs: object) -> _Callback:
    contexts = [formatting_repeatability(value) for value in inputs if value is not None]
    if any(context is None for context in contexts):
        return callback
    proven = tuple(context for context in contexts if context is not None)
    if not proven:
        return callback
    return _register_repeatable(
        callback,
        FormattingRepeatability(
            tuple(item for context in proven for item in context.selection),
            tuple(root for context in proven for root in context.roots),
            tuple(worker for context in proven for worker in context.workers),
            tuple(token for context in proven for token in context.tokens),
        ),
    )


def _invalidate_repeatability(context: FormattingRepeatability) -> None:
    # A degraded rendering is not repeatable evidence for a later type refusal.
    _INVALID_REPEATABILITY.update(context.tokens)
