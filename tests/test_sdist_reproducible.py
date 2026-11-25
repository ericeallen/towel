# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.


"""Two clean builds of one source tree give the same sdist, byte for byte.

setuptools alone dates each member by when its file was written and the gzip
header by when it ran, so the 2.0 release's two sdists differed in those fields.
"""

from __future__ import annotations

import gzip
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from typing import List, Tuple

import pytest

from scripts.sdist_normalize import normalize_sdist, source_date_epoch
from tests.test_import_model import _uv, requires_uv

REPOSITORY = Path(__file__).resolve().parents[1]
EPOCH = 1_700_000_000


def _archive(path: Path, *, written: int, owner: int, mode: int) -> Path:
    """A ``.tar.gz`` of one directory and file, as a machine with these habits would write it."""
    output = io.BytesIO()
    with gzip.GzipFile(filename=path.name, mode="wb", fileobj=output, mtime=written) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, data in (("root", None), ("root/" + "long-name-" * 12, b"text\n")):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE if data is None else tarfile.REGTYPE
                member.size = len(data or b"")
                member.mtime, member.mode = written + 0.5, mode
                member.uid = member.gid = owner
                member.uname = member.gname = f"user{owner}"
                archive.addfile(member, None if data is None else io.BytesIO(data))
    path.write_bytes(output.getvalue())
    return path


def test_normalizing_removes_everything_the_building_machine_put_there(tmp_path: Path) -> None:
    first = _archive(tmp_path / "a.tar.gz", written=1_800_000_000, owner=501, mode=0o644)
    second = _archive(tmp_path / "b.tar.gz", written=1_900_000_000, owner=1000, mode=0o664)
    assert first.read_bytes() != second.read_bytes()
    normalize_sdist(first, EPOCH)
    normalize_sdist(second, EPOCH)
    assert first.read_bytes() == second.read_bytes()
    assert int.from_bytes(first.read_bytes()[4:8], "little") == EPOCH
    with tarfile.open(first) as archive:
        directory, file = archive.getmembers()
        assert (directory.mtime, directory.mode, directory.uid) == (EPOCH, 0o755, 0)
        assert (file.mtime, file.mode, file.uname) == (EPOCH, 0o644, "")
        stream = archive.extractfile(file)
        assert stream is not None and stream.read() == b"text\n"


def test_normalizing_refuses_a_link(tmp_path: Path) -> None:
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("link")
        member.type, member.linkname = tarfile.SYMTYPE, "elsewhere"
        archive.addfile(member)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="link"):
        normalize_sdist(path, EPOCH)
    assert path.read_bytes() == before


def test_source_date_epoch_is_seconds_or_unset() -> None:
    assert source_date_epoch({}) is None
    assert source_date_epoch({"SOURCE_DATE_EPOCH": str(EPOCH)}) == EPOCH
    for wrong in ("", "0", "-1", "1.5", " 7", "yesterday", str(2**32)):
        with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
            source_date_epoch({"SOURCE_DATE_EPOCH": wrong})


def _copies(tmp_path: Path) -> Tuple[Path, Path]:
    """Two copies of the tracked tree whose files carry different times."""
    if shutil.which("git") is None or not (REPOSITORY / ".git").exists():
        pytest.skip("not a git checkout, so there is no list of tracked files")
    listed = subprocess.run(
        ["git", "-C", str(REPOSITORY), "ls-files", "-z"], capture_output=True, check=True
    )
    names = [name for name in listed.stdout.decode("utf-8").split("\0") if name]
    copies: List[Path] = []
    for index, written in enumerate((1_800_000_000, 1_900_000_000)):
        root = tmp_path / f"copy{index}"
        for name in names:
            source, target = REPOSITORY / name, root / name
            if not source.is_file():
                continue  # deleted in the working tree, or a submodule
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, target)
            os.utime(target, (written, written))
        copies.append(root)
    return copies[0], copies[1]


@requires_uv
def test_two_clean_builds_give_the_same_sdist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    built: List[bytes] = []
    for project in _copies(tmp_path):
        output = project.with_name(project.name + "-dist")
        _uv("build", "--sdist", "-q", "-o", str(output), str(project))
        (sdist,) = output.glob("*.tar.gz")
        built.append(sdist.read_bytes())
        with tarfile.open(sdist) as archive:
            assert {member.mtime for member in archive} == {EPOCH}
    assert built[0] == built[1]
