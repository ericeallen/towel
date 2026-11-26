# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Rewrite a source distribution so that its bytes depend only on its contents.

setuptools stamps each tar member with the time its file was written, the gzip
header with the time of the build, and every owner with the builder's account.
It reads ``SOURCE_DATE_EPOCH`` for wheels only. Standard library only: this
runs inside the build backend.
"""

from __future__ import annotations

import gzip
import io
import os
from pathlib import Path
import re
import tarfile
from typing import Mapping, Optional

COMPRESSION = 9
"""Named because the interpreters Towel supports do not share one gzip default."""
LATEST_EPOCH = 2**32 - 1
"""The gzip header holds its modification time in four bytes."""


def source_date_epoch(environment: Mapping[str, str]) -> Optional[int]:
    """The pinned build time, ``None`` if unset; any other value but a count of seconds fails."""
    raw = environment.get("SOURCE_DATE_EPOCH")
    if raw is None:
        return None
    if not re.fullmatch(r"[0-9]+", raw) or not 0 < int(raw) <= LATEST_EPOCH:
        raise ValueError(f"SOURCE_DATE_EPOCH must be a Unix time in seconds, not {raw!r}")
    return int(raw)


def _pinned(member: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    """``member`` with its name, type and size, and nothing taken from the building machine."""
    if not (member.isfile() or member.isdir()):
        raise ValueError(f"Source distribution holds a link or special file: {member.name}")
    result = tarfile.TarInfo(member.name)
    result.type, result.size, result.mtime = member.type, member.size, epoch
    # The checkout's umask decides the other permission bits.
    result.mode = 0o755 if member.isdir() or member.mode & 0o100 else 0o644
    return result


def normalize_sdist(archive: Path, epoch: int) -> None:
    """Replace ``archive``, a ``.tar.gz``, with the same files in the same order, dated ``epoch``."""
    output = io.BytesIO()
    with tarfile.open(archive, "r:gz") as source:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=output, mtime=epoch, compresslevel=COMPRESSION
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target:
                for member in source:
                    content = source.extractfile(member) if member.isfile() else None
                    target.addfile(_pinned(member, epoch), content)
    partial = archive.with_name(archive.name + ".partial")
    partial.write_bytes(output.getvalue())
    os.replace(partial, archive)
