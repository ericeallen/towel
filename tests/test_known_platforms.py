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

"""The standard library some platform or version lacks, held to the interpreter the suite runs on.

``known_platforms`` was read from the Availability notes of the CPython
3.11.15, 3.12.13 and 3.13.7 documentation. Every entry that names the
platform the suite runs on as lacking it is asked of this interpreter, and
so are the version lists, so a Python that disagrees fails here and returns
the list for review.
"""

from __future__ import annotations

import importlib
import sys
from fnmatch import fnmatchcase

import pytest

from towel.unification.known_platforms import (
    DESKTOP_PLATFORMS,
    NEEDS_TK,
    PLATFORM_ONLY,
    STDLIB_ON_EVERY_VERSION,
    STDLIB_ON_SOME_VERSIONS,
    PlatformOnly,
    platform_only,
    stdlib_everywhere,
)

_HERE = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform)
_VERSION = f"{sys.version_info[0]}.{sys.version_info[1]}"


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
    except ImportError:
        return False
    return True


def _missing_here(entry: PlatformOnly) -> bool:
    documented = entry.present_on.get(_VERSION)
    return _HERE is not None and documented is not None and _HERE not in documented


_MISSING_HERE = [entry for entry in PLATFORM_ONLY if _missing_here(entry)]
"""The entries whose documentation says this interpreter's platform lacks them."""


def test_every_entry_is_read_from_a_note_that_leaves_a_desktop_platform_out() -> None:
    for entry in PLATFORM_ONLY:
        assert entry.source.endswith(".rst") and set(entry.present_on) <= {"3.11", "3.12", "3.13"}
        assert any(platforms != DESKTOP_PLATFORMS for platforms in entry.present_on.values())
    assert _HERE is None or _MISSING_HERE


@pytest.mark.parametrize("entry", _MISSING_HERE, ids=[entry.name for entry in _MISSING_HERE])
def test_each_entry_is_missing_where_its_documentation_says(entry: PlatformOnly) -> None:
    if entry.is_module:
        assert not _importable(entry.name)
        return
    module, _, attribute = entry.name.rpartition(".")
    if not _importable(module):
        return  # its module is missing here too
    present = [
        name for name in dir(importlib.import_module(module)) if fnmatchcase(name, attribute)
    ]
    assert not present, present


def test_tkinter_is_there_exactly_where_tk_is() -> None:
    tk = _importable("_tkinter")
    assert {name: _importable(name) for name in NEEDS_TK} == {name: tk for name in NEEDS_TK}


@pytest.mark.skipif(_VERSION not in STDLIB_ON_SOME_VERSIONS, reason="not a version read")
def test_the_version_lists_are_this_interpreters() -> None:
    names = set(sys.stdlib_module_names)
    assert STDLIB_ON_EVERY_VERSION <= names
    assert names - STDLIB_ON_EVERY_VERSION == STDLIB_ON_SOME_VERSIONS[_VERSION]


@pytest.mark.parametrize(
    "dotted, lacking",
    [
        ("msvcrt", True),
        ("winreg", True),
        ("fcntl", True),
        ("curses.ascii", True),
        ("tkinter.ttk", True),
        ("os.startfile", True),
        ("os.fork", True),
        ("signal.CTRL_C_EVENT", True),
        ("signal.SIGALRM", True),
        ("ctypes.windll", True),
        ("socket.CAN_RAW", True),
        ("os", False),
        ("os.path.join", False),
        ("signal.SIGINT", False),
        ("subprocess.Popen", False),
        ("socket.AF_INET", False),
        ("json", False),
    ],
)
def test_platform_only_names(dotted: str, lacking: bool) -> None:
    assert platform_only(dotted) is lacking


def test_a_module_one_version_lacks_is_not_everywhere() -> None:
    assert not any(stdlib_everywhere(name) for name in ("distutils", "imp", "cgi", "msvcrt"))
    assert all(stdlib_everywhere(name) for name in ("json", "os", "tomllib", "typing"))
