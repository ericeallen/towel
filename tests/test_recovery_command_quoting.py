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

"""Every executable recovery remedy passes an ordinary or hostile path as one argument."""

from __future__ import annotations

from pathlib import Path
import shlex
import stat
import subprocess

import pytest

from towel.changes import (
    ExternalEdit,
    _distrust,
    _instead_of_recovery,
    _recovery_command,
    _set_aside,
    pending_journal_remedy,
)
from towel.cli import _build_parser


@pytest.mark.parametrize(
    "name", ["plain", "Recovery Workspace", "a'b", "x;echo nope", "$(echo nope)", "-options"]
)
def test_recovery_command_round_trips_through_shell_and_actual_parser(
    tmp_path: Path, name: str
) -> None:
    journal = tmp_path / name / ".towel-transaction-probe"
    journal.parent.mkdir()
    journal.mkdir(mode=0o700)
    (journal / "manifest.json").write_text("[]\n")
    command = _recovery_command(journal)
    arguments = shlex.split(command)
    assert arguments == ["towel", "recover", "--", str(journal)]
    assert _build_parser().parse_args(arguments[1:]).journal == journal
    remedy = pending_journal_remedy(journal)
    assert remedy.endswith("recover it first: " + command)
    assert command in _instead_of_recovery(journal, ExternalEdit("external edit"))
    journal.chmod(0o755)
    distrust = _distrust(journal)
    assert distrust is not None and command in distrust
    chmod = shlex.join(("chmod", "--", "700", str(journal)))
    assert chmod in distrust
    changed_mode = subprocess.run(shlex.split(chmod), capture_output=True, text=True, check=False)
    assert changed_mode.returncode == 0, changed_mode.stderr
    assert stat.S_IMODE(journal.stat().st_mode) == 0o700
    moved = subprocess.run(
        shlex.split(_set_aside(journal)), capture_output=True, text=True, check=False
    )
    assert moved.returncode == 0, moved.stderr
    aside = Path(shlex.split(_set_aside(journal))[-1])
    assert not journal.exists() and (aside / "manifest.json").read_text() == "[]\n"


def test_relative_option_like_journal_is_a_positional_argument() -> None:
    journal = Path("-project/.towel-transaction-probe")
    arguments = shlex.split(_recovery_command(journal))
    assert _build_parser().parse_args(arguments[1:]).journal == journal
