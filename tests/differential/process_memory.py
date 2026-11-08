# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Sample an owned observer's current resident memory without spawning ps.

macOS exposes the same task RSS through libproc that ps reads. Reading one PID
requires no executable path or command-line inspection. This is sampled current
RSS, not peak memory, physical footprint, or the sum of a process tree. A child
can exceed the budget between samples; the harness documents that limitation.
"""

from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os
from pathlib import Path
import sys


class _TaskInfo(ctypes.Structure):
    """struct proc_taskinfo from Apple's sys/proc_info.h; resident size is bytes."""

    pti_resident_size: int
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


@lru_cache(maxsize=1)
def _libproc() -> ctypes.CDLL:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    library.proc_pidinfo.restype = ctypes.c_int
    return library


def _task_info(pid: int, info: _TaskInfo) -> tuple[int, int]:
    """Return the native byte count and this call's errno, never a stale errno."""
    function = _libproc().proc_pidinfo
    ctypes.set_errno(0)
    count = int(function(pid, 4, 0, ctypes.byref(info), ctypes.sizeof(info)))
    return count, ctypes.get_errno()


def _macos_resident_bytes(pid: int) -> int:
    info = _TaskInfo()
    size = ctypes.sizeof(info)
    count, error = _task_info(pid, info)
    if count == size:
        return info.pti_resident_size
    if count <= 0 and error == errno.ESRCH:
        return 0  # The owned child exited between poll and sampling.
    if count <= 0:
        number = error or errno.EIO
        raise OSError(number, f"Cannot measure observer {pid} RSS: {os.strerror(number)}")
    raise OSError(
        errno.EIO,
        f"Cannot measure observer {pid} RSS: proc_pidinfo returned {count} of {size} bytes",
    )


def resident_bytes(pid: int) -> int:
    """Read one owned child's RSS; only a missing/exited process is zero memory."""
    if sys.platform == "darwin":
        return _macos_resident_bytes(pid)
    elif sys.platform == "linux":
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except FileNotFoundError:
            return 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        return 0  # An exited/zombie Linux process has no VmRSS field.
    else:
        raise RuntimeError(f"Cannot measure observer RSS on unsupported platform {sys.platform}")
