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

from pathlib import Path
from unittest.mock import patch

import pytest

from towel.filesystem import copy_project


def test_copy_failure_leaves_new_destination_absent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "original.py").write_text("value = 1\n")
    output = tmp_path / "output"

    def partial_copy(src, dst, **kwargs):
        dst.mkdir()
        (dst / "partial").write_text("incomplete")
        raise OSError("disk full")

    with patch("towel.filesystem.shutil.copytree", side_effect=partial_copy):
        with pytest.raises(OSError):
            copy_project(source, output)
    assert not output.exists()
    assert (source / "original.py").read_text() == "value = 1\n"
    assert not list(tmp_path.glob(".towel-copy-*"))


def test_directory_copy_preserves_symlinks_without_following(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "external"
    outside.write_text("untouched")
    (source / "alias").symlink_to(outside)
    output = tmp_path / "output"
    output.mkdir()
    copy_project(source, output, allow_empty=True)
    assert (output / "alias").is_symlink()
    assert outside.read_text() == "untouched"
