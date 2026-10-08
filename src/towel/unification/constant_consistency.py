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

"""Exact constant identities for equality and differing-column reuse."""

from __future__ import annotations

from typing import Hashable

from .unifier_state import ConstantIdentity


def constant_identity(value: object) -> ConstantIdentity:
    """What makes two constants interchangeable: the same type and the same value.

    Equality is not enough. ``0 == 0.0 == False`` and ``1 == True``, and
    each pair hashes alike, so a set or a dict keyed by value merges them;
    a helper that hard-codes one block's ``0`` then returns ``0`` where the
    other block returned ``0.0`` or ``False``, which print differently and
    are told apart by ``type``, ``is`` and every serializer. A float or
    complex is identified by its ``repr``, which tells ``-0.0`` from ``0.0``
    and makes every NaN one constant, where equality does neither. An int
    is identified by value, never by its decimal spelling, which CPython
    refuses for a very wide int.
    """
    if isinstance(value, (float, complex)):
        return (type(value), repr(value))
    if isinstance(value, tuple):
        return (type(value), tuple(constant_identity(member) for member in value))
    if isinstance(value, frozenset):
        return (type(value), frozenset(constant_identity(member) for member in value))
    return (type(value), value if isinstance(value, Hashable) else repr(value))
