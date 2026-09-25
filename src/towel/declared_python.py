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

"""The Python versions a project says it runs on: the oldest and the newest it admits.

A version requirement (``requires-python`` in ``[project]``, setup.cfg's
``python_requires``, Poetry's ``python`` dependency) bounds both ends. Where
it leaves the top open, as ``>=3.9`` does, the ``Programming Language ::
Python :: 3.N`` classifiers name the newest version the project declares.
Nothing here reads a ``setup.py``, which only running it could answer.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

from .project_layout import load_pyproject

PythonVersion = Tuple[int, int]
"""A Python version to the minor version: ``(3, 12)``."""

_VERSION_CLAUSE = re.compile(
    r"(?P<operator>===|==|~=|>=|<=|!=|>|<|\^|~)\s*v?(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?(?P<rest>(?:\.(?:\d+|\*))*)(?:[a-z]+\d*)?(?:\.\*)?"
)

_PYTHON_CLASSIFIER = re.compile(r"Programming Language :: Python :: (\d+)\.(\d+)")


def python_lower_bound(specifier: str) -> Optional[PythonVersion]:
    """The oldest Python a version requirement admits, to the minor version; None if unbounded.

    PEP 440 clauses, comma-separated, as ``requires-python`` spells them, and
    Poetry's ``^3.9`` and ``~3.9``: ``>=``, ``~=``, ``==``, ``===``, ``^`` and
    ``~`` bound from below at their version, ``>`` at the same minor version
    (``>3.8`` admits 3.8.1), and the tightest bound wins. ``<``, ``<=`` and
    ``!=`` bound nothing below. Anything unreadable makes the whole answer
    None, which the caller takes as the oldest Python there is.
    """
    bounds: List[PythonVersion] = []
    for clause in (part.strip() for part in specifier.split(",")):
        if not clause:
            continue
        match = _VERSION_CLAUSE.fullmatch(clause)
        if match is None:
            return None
        if match.group("operator") in {"<", "<=", "!="}:
            continue
        bounds.append((int(match.group("major")), int(match.group("minor") or 0)))
    return max(bounds) if bounds else None


def python_upper_bound(specifier: str) -> Optional[PythonVersion]:
    """The newest Python a version requirement admits, to the minor version; None if unbounded.

    ``<3.13`` and ``<3.13.0`` admit 3.12 at most, and so do ``<3.12.5``,
    ``<=3.12``, ``==3.12.*``, ``===3.12.1``, ``~=3.12.1`` and Poetry's
    ``~3.12``; the tightest bound wins. A bound on the major version alone
    (``<4``, ``~=3.9``, Poetry's ``^3.9``, ``==3.*``) names no newest minor
    version, and ``>=``, ``>`` and ``!=`` bound nothing above. Anything
    unreadable makes the whole answer None.
    """
    bounds: List[PythonVersion] = []
    for clause in (part.strip() for part in specifier.split(",")):
        if not clause:
            continue
        match = _VERSION_CLAUSE.fullmatch(clause)
        if match is None:
            return None
        bound = _clause_upper_bound(
            match.group("operator"),
            int(match.group("major")),
            None if match.group("minor") is None else int(match.group("minor")),
            [part for part in match.group("rest").split(".") if part],
        )
        if bound is not None:
            bounds.append(bound)
    return min(bounds) if bounds else None


def _clause_upper_bound(
    operator: str, major: int, minor: Optional[int], rest: Sequence[str]
) -> Optional[PythonVersion]:
    """The newest minor version one clause admits; None where it bounds no minor version above."""
    if minor is None or operator in {">", ">=", "!=", "^"}:
        return None
    if operator in {"==", "===", "<=", "~"}:
        return (major, minor)
    if operator == "~=":
        # ``~=3.12.1`` is ``>=3.12.1, ==3.12.*``; ``~=3.12`` is ``==3.*``.
        return (major, minor) if rest else None
    # ``<``: below 3.13.0 is 3.12 at most, below 3.12.5 is 3.12 still.
    micro = rest[0] if rest else "0"
    if micro.isdigit() and int(micro) > 0:
        return (major, minor)
    return (major, minor - 1) if minor > 0 else None


def declared_requirement(root: Path) -> Optional[str]:
    """The project's version requirement on Python, where it declares one it can be read from.

    ``requires-python`` in ``[project]`` first, since that is the promise the
    package makes to whoever installs it; else setup.cfg's
    ``python_requires``; else Poetry's ``python`` dependency.
    """
    pyproject = load_pyproject(root)
    requirement: object = _table(pyproject, "project").get("requires-python")
    if not isinstance(requirement, str) and (root / "setup.cfg").is_file():
        requirement = _ini_option(root / "setup.cfg", "options", "python_requires")
    if not isinstance(requirement, str):
        requirement = _table(pyproject, "tool", "poetry", "dependencies").get("python")
    return requirement if isinstance(requirement, str) else None


def declared_newest_python(root: Path) -> Optional[PythonVersion]:
    """The newest Python the project at ``root`` says it supports, or None when it says nothing.

    The requirement's upper bound where it has one (:func:`python_upper_bound`),
    else the newest ``Programming Language :: Python :: 3.N`` classifier of
    ``[project]``, Poetry's table or setup.cfg's ``[metadata]``.
    """
    requirement = declared_requirement(root)
    bound = python_upper_bound(requirement) if requirement is not None else None
    if bound is not None:
        return bound
    classified = [
        (int(match.group(1)), int(match.group(2)))
        for classifier in _classifiers(root)
        if (match := _PYTHON_CLASSIFIER.fullmatch(classifier.strip())) is not None
    ]
    return max(classified) if classified else None


def _classifiers(root: Path) -> List[str]:
    pyproject = load_pyproject(root)
    found: List[str] = []
    for table in (_table(pyproject, "project"), _table(pyproject, "tool", "poetry")):
        listed = table.get("classifiers")
        if isinstance(listed, list):
            found.extend(item for item in listed if isinstance(item, str))
    if (root / "setup.cfg").is_file():
        listed_text = _ini_option(root / "setup.cfg", "metadata", "classifiers")
        if listed_text is not None:
            found.extend(line for line in listed_text.splitlines() if line.strip())
    return found


def _table(document: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    """``document[keys[0]][keys[1]]...``, or an empty table where any step is not one."""
    node: object = document
    for key in keys:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _ini_option(path: Path, section: str, option: str) -> Optional[str]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return None
    return parser.get(section, option, fallback=None)
