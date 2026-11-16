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

"""Runtime audit items 4, 7 and 10: bounded private copies and early failures."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from towel.filesystem import ProjectTooLarge, refuse_unusable_output, staged_project


def test_preview_filters_payload_but_can_change_hard_links(tmp_path: Path) -> None:
    """Runtime item 4: preview copies source/config, not repository and environment payloads."""
    root = tmp_path / "project"
    root.mkdir()
    module = root / "m.py"
    module.write_text("x = 1\n")
    (root / "alias.py").hardlink_to(module)
    (root / "pyproject.toml").write_text("")
    (root / "data.bin").write_bytes(b"payload")
    for name in (".git", "node_modules", ".venv"):
        directory = root / name
        directory.mkdir()
        (directory / "unwanted.py").write_text("x = 9\n")
    (root / ".venv" / "pyvenv.cfg").write_text("")
    with staged_project(root, root, tmp_path / "out", limit=100, preview=True) as stage:
        assert sorted(p.name for p in stage.target.iterdir()) == [
            "alias.py",
            "m.py",
            "pyproject.toml",
        ]
        assert not stage.in_place
        assert not stage.hard_linked
        assert module in stage.staged
        (stage.target / "m.py").write_text("x = 2\n")
    assert module.read_text() == "x = 1\n"
    assert not (tmp_path / "out").exists()


def test_output_parent_is_probed_before_analysis(tmp_path: Path) -> None:
    """Runtime item 7: reject an unwritable existing ancestor without making output parents."""
    source = tmp_path / "source.py"
    source.write_text("x = 1\n")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="Cannot write output.*locked"):
            refuse_unusable_output(source, locked / "missing" / "out.py")
        assert not (locked / "missing").exists()
    finally:
        locked.chmod(0o700)


def test_output_parent_that_is_a_file_is_refused(tmp_path: Path) -> None:
    """Runtime item 7: an output beneath a regular file cannot be published."""
    source = tmp_path / "source.py"
    source.write_text("x = 1\n")
    blocker = tmp_path / "blocker"
    blocker.write_text("occupied")
    with pytest.raises(ValueError, match="Cannot write output"):
        refuse_unusable_output(source, blocker / "out.py")


def test_large_project_message_names_the_count_and_working_remedy(tmp_path: Path) -> None:
    """Runtime item 7: count source and stub files; explain the project boundary."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "m.py").write_text("")
    (root / "m.pyi").write_text("")
    with pytest.raises(ProjectTooLarge, match=r"more than 1 Python source/stub files.*\.py.*\.pyi"):
        with staged_project(root, root, tmp_path / "out", limit=1):
            pass


def test_next_run_removes_only_a_dead_owned_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime item 10: a killed run leaves an identifiable stage, reclaimed next time."""
    temporary = tmp_path / "temp"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    root = tmp_path / "project"
    root.mkdir()
    (root / "m.py").write_text("x = 1\n")
    code = (
        "import os, sys, tempfile; from pathlib import Path; "
        "from towel.filesystem import staged_project; "
        "tempfile.tempdir = sys.argv[1]; "
        "stage = staged_project(Path(sys.argv[2]), Path(sys.argv[2]), "
        "Path(sys.argv[3]), limit=100); stage.__enter__(); os._exit(0)"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(temporary), str(root), str(tmp_path / "out")],
        check=True,
    )
    dead = tuple(temporary.iterdir())
    assert len(dead) == 1
    unowned = temporary / "towel-stage-no-marker"
    unowned.mkdir()
    with staged_project(root, root, tmp_path / "out", limit=100) as live:
        assert not dead[0].exists()
        assert unowned.exists()
        with staged_project(root, root, tmp_path / "out2", limit=100):
            assert live.root.exists(), "a living owner must not lose its stage"
    assert list(temporary.iterdir()) == [unowned]


def test_large_full_copy_announces_size_before_copying(tmp_path: Path) -> None:
    """Runtime item 4: full out-of-place copies remain full and announce large payloads."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "m.py").write_text("x = 1\n")
    with (root / "large.bin").open("wb") as stream:
        stream.truncate(11_000_000)
    notices: list[str] = []
    with staged_project(root, root, tmp_path / "out", limit=100, notice=notices.append) as stage:
        assert (stage.target / "large.bin").stat().st_size == 11_000_000
    assert notices == [f"Copying 2 files (11.0 MB) to {tmp_path / 'out'}"]


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("directory_mode", [0, 0o400])
def test_relaxed_staging_skips_unreadable_inputs(
    tmp_path: Path, preview: bool, directory_mode: int
) -> None:
    """Runtime items 4/6: staging cannot defeat relaxed unreadable-input handling."""
    from towel.source_files import source_policy

    root = tmp_path / "project"
    root.mkdir()
    (root / "good.py").write_text("x = 1\n")
    locked_file = root / "locked.py"
    locked_file.write_text("x = 2\n")
    locked_directory = root / "locked_directory"
    locked_directory.mkdir()
    (locked_directory / "m.py").write_text("x = 3\n")
    locked_file.chmod(0)
    locked_directory.chmod(directory_mode)
    try:
        with source_policy(strict=False) as policy:
            with staged_project(root, root, tmp_path / "out", limit=100, preview=preview) as stage:
                assert (stage.target / "good.py").exists()
                assert not (stage.target / "locked.py").exists()
                assert not (stage.target / "locked_directory").exists()
            assert locked_file in policy.skipped
            assert locked_directory in policy.skipped
    finally:
        locked_file.chmod(0o600)
        locked_directory.chmod(0o700)


@pytest.mark.parametrize("directory", [False, True])
def test_full_copy_names_unreadable_inputs_missing_from_output(
    tmp_path: Path, directory: bool
) -> None:
    """D5: adopting a relaxed full copy must not silently lose unreadable input."""
    import os
    from tests.test_cli_integration import LITERAL_DUPLICATES, invoke

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / "good.py").write_text(LITERAL_DUPLICATES)
    locked = project / ("locked" if directory else "locked.py")
    if directory:
        locked.mkdir()
        (locked / "data.txt").write_text("keep me")
    else:
        locked.write_text("keep me")
    locked.chmod(0)
    output = tmp_path / "out"
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("Current user bypasses permissions")
        result = invoke(
            [
                "dry",
                str(project),
                str(output),
                "--no-types",
                "--no-format",
                "--no-interactive",
                "--quiet",
                "--no-strict",
            ]
        )
        assert result.status == 0, result
        assert "not copied to" in result.stderr
        assert str(output / locked.name) in result.stderr
        assert not (output / locked.name).exists()
    finally:
        locked.chmod(0o700 if directory else 0o600)
