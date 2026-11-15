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
3.11.15, 3.12.13, 3.13.7, 3.14.7 and 3.15.2 documentation. Every entry that names the
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
_READ_VERSIONS = {"3.11", "3.12", "3.13", "3.14", "3.15"}


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
        assert entry.source.endswith(".rst") and set(entry.present_on) <= _READ_VERSIONS
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


def test_every_supported_interpreter_has_a_complete_inventory() -> None:
    """Python remediation: a newly supported version must not skip inventory validation."""
    assert set(STDLIB_ON_SOME_VERSIONS) == _READ_VERSIONS
    for extra in STDLIB_ON_SOME_VERSIONS.values():
        assert not STDLIB_ON_EVERY_VERSION & extra


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
        ("encodings.win32_code_page_search_function", True),
        ("ctypes.CopyComPointer", True),
        ("ctypes.COMError", True),
        ("ctypes.util.find_msvcrt", True),
        ("os.statx", True),
        ("os.STATX_BTIME", True),
        ("os.RWF_DONTCACHE", True),
        ("socket.BDADDR_LE_PUBLIC", True),
        ("socket.HCI_CHANNEL_MONITOR", True),
        ("select.epoll", True),
        ("signal.SIGQUIT", True),
        ("resource.RLIMIT_PIPEBUF", True),
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
    assert not any(
        stdlib_everywhere(name)
        for name in (
            "distutils",
            "imp",
            "cgi",
            "msvcrt",
            "_compression",
            "sre_compile",
            "sre_constants",
            "sre_parse",
            "annotationlib",
            "compression",
            "profiling",
        )
    )
    assert all(stdlib_everywhere(name) for name in ("json", "os", "tomllib", "typing"))


def test_new_documentation_changes_platform_facts_independently() -> None:
    """Python remediation: new notes are read per release, not copied from the last one."""
    entries = {entry.name: entry for entry in PLATFORM_ONLY}
    assert entries["os.pipe2"].present_on["3.14"] == {"Linux"}
    assert entries["os.pipe2"].present_on["3.15"] == {"Linux", "macOS"}
    assert entries["os.lchmod"].present_on["3.14"] == {"macOS", "Windows"}
    assert entries["os.lchmod"].present_on["3.15"] == {"macOS", "Windows"}
    assert "3.14" not in entries["errno.ENOTCAPABLE"].present_on
    assert set(entries["os.statx"].present_on) == {"3.15"}
