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

"""Runtime audit R8–R10: discovery sees source, and unreadable input has a policy."""

from pathlib import Path
import subprocess
import sys

import pytest

from towel.source_files import python_sources
from towel.unification.builtin_mapping_writes import project_may_replace_builtin_mapping


@pytest.mark.parametrize("name", ["build", "dist", "site-packages", "demo.egg-info"])
def test_build_products_are_not_analysed(tmp_path: Path, name: str) -> None:
    """R10: generated installation and build trees do not enter extraction."""
    (tmp_path / name).mkdir()
    (tmp_path / name / "generated.py").write_text("x = 1\n")
    source = tmp_path / "main.py"
    source.write_text("x = 2\n")
    assert python_sources(tmp_path) == [source]


def test_gitignore_obeys_negation_and_tracked_files(tmp_path: Path) -> None:
    """R10: actual Git ignores, including negation, determine the default selection."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.py\n!keep.py\n")
    for name in ("keep.py", "tracked.py", "ignored.py"):
        (tmp_path / name).write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "tracked.py"], check=True)
    assert [p.name for p in python_sources(tmp_path)] == ["keep.py", "tracked.py"]


@pytest.mark.parametrize("name", ["dangling.py", ".#module.py"])
def test_dangling_links_do_not_disable_extraction(tmp_path: Path, name: str) -> None:
    """R8: an editor lock or dangling link is no executable module."""
    (tmp_path / name).symlink_to("missing-target")
    assert not project_may_replace_builtin_mapping(tmp_path)


def test_include_ignored_and_discovery_census(tmp_path: Path) -> None:
    """R10: callers can include ignored files; the census names each omitted source."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("local.py\n")
    (tmp_path / "local.py").write_text("x = 1\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "built.py").write_text("x = 1\n")
    engine = UnificationRefactorEngine(annotate_helpers=False)
    assert engine.analyze_directory(str(tmp_path), progress="none") == []
    assert {Path(s.path).name for s in engine.last_census.skipped} == {"local.py", "built.py"}
    assert python_sources(tmp_path, include_ignored=True) == [tmp_path / "local.py"]


@pytest.mark.parametrize("data", [b"def broken(:\n", b"x=1\0\n"])
def test_relaxed_nonprogram_files_warn_once_and_strict_retains_parser_policy(
    tmp_path: Path, data: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """R6/R9: relaxed invalid inputs warn once and cannot veto whole-program scans."""
    from towel.program_files import program_files, refuse_unparsed_program
    from towel.source_files import source_policy
    from towel.unification.exceptions import UnparsedProgramError

    source = tmp_path / "bad.py"
    source.write_bytes(data)
    (tmp_path / "pyproject.toml").write_text("")
    with source_policy(strict=True):
        if b"\0" in data and sys.version_info >= (3, 14):
            # Null bytes fail decoding here, which strict has always skipped.
            refuse_unparsed_program(tmp_path)
            assert source in list(program_files(tmp_path))
        else:
            with pytest.raises(UnparsedProgramError, match="--exclude bad.py"):
                refuse_unparsed_program(tmp_path)
    with source_policy(strict=False):
        refuse_unparsed_program(tmp_path)
        assert source not in list(program_files(tmp_path))
        assert not project_may_replace_builtin_mapping(tmp_path)
    assert (
        sum(
            "Skipping" in record.message and "bad.py" in record.message for record in caplog.records
        )
        == 1
    )


@pytest.mark.parametrize("directory,mode", [(False, 0), (True, 0), (True, 0o400)])
def test_unreadable_input_warns_with_its_own_name(
    tmp_path: Path, directory: bool, mode: int, caplog: pytest.LogCaptureFixture
) -> None:
    """R9: the unreadable directory itself is named, never its pyvenv.cfg marker."""
    import os
    from towel.program_files import refuse_unparsed_program
    from towel.source_files import source_policy

    (tmp_path / "pyproject.toml").write_text("")
    inaccessible = tmp_path / ("locked" if directory else "secret.py")
    if directory:
        inaccessible.mkdir()
        (inaccessible / "m.py").write_text("x = 1\n")
    else:
        inaccessible.write_text("x = 1\n")
    inaccessible.chmod(mode)
    try:
        if os.access(inaccessible, os.R_OK | (os.X_OK if directory else 0)):
            pytest.skip("Current user bypasses file permissions")
        with source_policy(strict=False):
            refuse_unparsed_program(tmp_path)
            python_sources(tmp_path)
        messages = [record.message for record in caplog.records if "Skipping" in record.message]
        assert len(messages) == 1
        assert str(inaccessible) in messages[0]
        assert "pyvenv.cfg" not in messages[0]
    finally:
        inaccessible.chmod(0o755 if directory else 0o644)


@pytest.mark.parametrize("mode", [0, 0o400])
def test_strict_unreadable_directory_remedy_works(tmp_path: Path, mode: int) -> None:
    """R9: excluding an unreadable directory clears the refusal that names it."""
    import os
    from towel.program_files import refuse_unparsed_program
    from towel.source_files import source_policy

    (tmp_path / "pyproject.toml").write_text("")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "m.py").write_text("x = 1\n")
    locked.chmod(mode)
    try:
        if os.access(locked, os.R_OK | os.X_OK):
            pytest.skip("Current user bypasses directory permissions")
        with source_policy(strict=True), pytest.raises(PermissionError, match="--exclude locked"):
            refuse_unparsed_program(tmp_path)
        with source_policy(strict=True):
            refuse_unparsed_program(tmp_path, ["locked"])
    finally:
        locked.chmod(0o755)


@pytest.mark.parametrize("command", ["preview", "dry", "quick"])
def test_cli_aliases_do_not_duplicate_unreadable_inputs(tmp_path: Path, command: str) -> None:
    """R6/R9: relaxed original, alias and stage paths identify one unavailable input."""
    import json
    from tests.test_cli_integration import LITERAL_DUPLICATES, invoke

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / "good.py").write_text(LITERAL_DUPLICATES)
    (project / "bad.py").write_text("def broken(:\n")
    locked = project / "locked"
    locked.mkdir()
    (locked / "m.py").write_text("x = 1\n")
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    locked.chmod(0o400)
    try:
        arguments = ["preview" if command == "quick" else command, str(alias)]
        arguments.extend(["--quick"] if command == "quick" else ["--json"])
        arguments.extend(["--no-types", "--no-format", "--no-strict"])
        if command == "dry":
            arguments.extend([str(tmp_path / "out"), "--no-interactive"])
        result = invoke(arguments)
        assert result.status == 0, result
        assert result.stderr.count("Skipping ") == 2, result.stderr
        if command == "quick":
            assert "Skipped 2 files it could not read or parse." in result.stdout
        else:
            payload = json.loads(result.stdout)
            assert payload["applied"] > 0
            assert len(payload["analysed"]["skipped"]) == 2, payload["analysed"]
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("command", ["preview", "dry"])
@pytest.mark.parametrize("data", [b"x = '\xe9'\n", b"# coding: unknown_codec\nx = 1\n"])
def test_strict_preserves_undecodable_skip(tmp_path: Path, command: str, data: bytes) -> None:
    """D4: strict retains 1.949's undecodable skip instead of a new refusal."""
    from tests.test_cli_integration import LITERAL_DUPLICATES, invoke

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / "good.py").write_text(LITERAL_DUPLICATES)
    (project / "bad.py").write_bytes(data)
    args = [command, str(project), "--strict", "--no-types", "--no-format"]
    if command == "dry":
        args += [str(tmp_path / "out"), "--no-interactive"]
    result = invoke(args)
    assert result.status == 0, result
    assert "Skipping" in result.stderr and "bad.py" in result.stderr
    assert (project / "bad.py").read_bytes() == data
    if command == "dry":
        assert (tmp_path / "out" / "bad.py").read_bytes() == data


@pytest.mark.parametrize("failure", ["timeout", "ownership"])
def test_unavailable_git_ignore_rules_warn_and_retain_sources(
    tmp_path: Path, failure: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 2 suspicion: Git discovery failures do not abort an otherwise readable project."""
    from towel.source_files import ignored_by_git

    path = tmp_path / "m.py"
    path.write_text("x = 1\n")

    def unavailable(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired("git", 10)
        return subprocess.CompletedProcess("git", 128, b"", b"fatal: detected dubious ownership")

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert ignored_by_git(tmp_path, [path]) == frozenset()
    assert "Cannot read Git ignore rules" in caplog.text
    assert "including files Git may ignore" in caplog.text


@pytest.mark.parametrize("excluded", ["bad.py", "fixtures"])
def test_excluded_invalid_source_is_not_reported_as_unavailable(
    tmp_path: Path, excluded: str
) -> None:
    """Integration 3: excluded syntax errors are intentional omissions, not read failures."""
    from tests.test_cli_integration import LITERAL_DUPLICATES, invoke

    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "good.py").write_text(LITERAL_DUPLICATES)
    bad = tmp_path / ("bad.py" if excluded == "bad.py" else "fixtures/bad.py")
    bad.parent.mkdir(exist_ok=True)
    bad.write_text("def broken(:\n")
    result = invoke(
        [
            "preview",
            str(tmp_path),
            "--no-strict",
            "--no-types",
            "--no-format",
            "--exclude",
            excluded,
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    assert "Would apply 1 refactoring" in result.stdout
    assert "Skipping " not in result.stderr
    assert "could not read or parse" not in result.stdout
    assert bad.read_text() == "def broken(:\n"


@pytest.mark.parametrize("invalid", [False, True])
def test_explicit_file_target_retains_precedence_over_its_excluded_name(
    tmp_path: Path, invalid: bool
) -> None:
    """Integration 3: exclusion during discovery must not bypass an explicit target's policy."""
    from tests.test_cli_integration import LITERAL_DUPLICATES, invoke

    target = tmp_path / "target.py"
    original = "def broken(:\n" if invalid else LITERAL_DUPLICATES
    target.write_text(original)
    result = invoke(
        [
            "preview",
            str(target),
            "--no-strict",
            "--no-types",
            "--no-format",
            "--exclude",
            target.name,
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    if invalid:
        assert result.stderr.count("Skipping ") == 1
        assert str(target) in result.stderr
        assert "Skipped 1 file it could not read or parse" in result.stdout
    else:
        assert "Would apply 1 refactoring" in result.stdout
        assert "Skipping " not in result.stderr
    assert target.read_text() == original
