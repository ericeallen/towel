"""Compare two observations and say which differences are changes of behaviour.

The observer's records (:mod:`tests.differential.observer`) are compared
structurally. Lists of names are compared as sets, so a name Towel adds is
reported as an addition rather than as a shifted list; a class's MRO is
compared in order as well.

A difference is tolerated, rather than counted as a change of behaviour,
only where Towel documents it:

- a private name (one with a leading underscore) added to a module or a
  class: a helper, or the private alias a helper's annotations are read
  through. A public name added, dropped or rebound reaches every importer
  and every ``import *``, and is a change of behaviour;
- with ``--cross-module``, a project module that importing another now
  loads too, and its description: the new import edge the option
  documents. A module no longer loaded is never tolerated, and without the
  option no difference in which project modules load is.

Everything else, including any stdout, stderr, warning, value, exception
or signature that differs, is a change of behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, List, Literal, Tuple

DifferenceKind = Literal["value", "added", "removed", "set", "order", "length"]

_SET_LISTS = frozenset({"names", "dir", "dict_keys", "new_modules", "project_modules", "mro"})
_NAME_LISTS = frozenset({"names", "dir", "dict_keys"})
_MODULE_LISTS = frozenset({"new_modules", "project_modules"})
_ADDED_ITEM = re.compile(r"^/modules(_after)?/[^/]+/items/[^/]+$")
_ADDED_MODULE = re.compile(r"^/modules(_after)?/[^/]+$")
_ADDED_MEMBER = re.compile(r"/members/[^/]+$")
_SHOWN = 600


def _shown(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "..."


@dataclass(frozen=True)
class Difference:
    """One place where the two observations disagree."""

    path: str
    kind: DifferenceKind
    before: str = ""
    after: str = ""
    removed: Tuple[str, ...] = ()
    added: Tuple[str, ...] = ()

    def render(self) -> str:
        """The difference as a line or two a person can read in a failure message."""
        if self.kind == "set":
            return f"{self.path}: removed {list(self.removed)}, added {list(self.added)}"
        if self.kind == "added":
            return f"{self.path}: only after: {self.after}"
        if self.kind == "removed":
            return f"{self.path}: only before: {self.before}"
        return f"{self.path}:\n    before: {self.before}\n    after:  {self.after}"


def differences(before: Any, after: Any, path: str = "") -> List[Difference]:
    """Every difference between two observation records, by path."""
    if isinstance(before, dict) and isinstance(after, dict):
        found: List[Difference] = []
        for key in sorted(set(before) | set(after)):
            where = f"{path}/{key}"
            if key not in before:
                found.append(Difference(where, "added", after=_shown(after[key])))
            elif key not in after:
                found.append(Difference(where, "removed", before=_shown(before[key])))
            else:
                found += differences(before[key], after[key], where)
        return found
    if isinstance(before, list) and isinstance(after, list):
        last = path.rsplit("/", 1)[-1]
        if last in _SET_LISTS:
            was, now = set(map(str, before)), set(map(str, after))
            if was != now:
                return [
                    Difference(
                        path,
                        "set",
                        removed=tuple(sorted(was - now)),
                        added=tuple(sorted(now - was)),
                    )
                ]
            if last == "mro" and before != after:
                return [Difference(path, "order", before=_shown(before), after=_shown(after))]
            return []
        if len(before) != len(after):
            return [Difference(path, "length", before=_shown(before), after=_shown(after))]
        found = []
        for index, (was_item, now_item) in enumerate(zip(before, after)):
            found += differences(was_item, now_item, f"{path}[{index}]")
        return found
    if before != after:
        return [Difference(path, "value", before=_shown(before), after=_shown(after))]
    return []


def _private(name: str) -> bool:
    return name.startswith("_")


def tolerated(difference: Difference, *, cross_module: bool) -> bool:
    """Whether ``difference`` is one Towel documents, rather than a change of behaviour."""
    last = difference.path.rsplit("/", 1)[-1]
    if difference.kind == "set" and not difference.removed:
        if last in _NAME_LISTS:
            return all(_private(name) for name in difference.added)
        if last in _MODULE_LISTS:
            return cross_module
    if difference.kind == "added":
        if _ADDED_ITEM.match(difference.path) or _ADDED_MEMBER.search(difference.path):
            return _private(last)
        return cross_module and bool(_ADDED_MODULE.match(difference.path))
    return False


def behaviour_changes(before: Any, after: Any, *, cross_module: bool) -> Tuple[Difference, ...]:
    """The differences between two observations that are changes of behaviour."""
    return tuple(
        difference
        for difference in differences(before, after)
        if not tolerated(difference, cross_module=cross_module)
    )
