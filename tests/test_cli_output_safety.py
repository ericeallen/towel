"""CLI output operations must preserve existing files and honor cancellation."""

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.cli import _run_dry


def arguments(source: Path, destination: Path) -> argparse.Namespace:
    return argparse.Namespace(
        input=str(source),
        output=str(destination),
        interactive=True,
        prefer_absolute_imports=None,
        pep420=None,
        max_refactorings=1,
        progress="none",
    )


def test_existing_output_preserved(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "output"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("valuable")
    with pytest.raises(ValueError, match="already exists"):
        _run_dry(arguments(source, destination))
    assert sentinel.read_text() == "valuable"


@pytest.mark.parametrize("ancestor", [True, False])
def test_overlapping_paths_rejected(tmp_path: Path, ancestor: bool) -> None:
    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "keep.py"
    sentinel.write_text("x = 1\n")
    destination = tmp_path if ancestor else source / "output"
    with pytest.raises(ValueError, match="contain"):
        _run_dry(arguments(source, destination))
    assert sentinel.read_text() == "x = 1\n"
    assert not (source / "output").exists()


def test_cancel_does_not_create_output(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("x = 1\n")
    destination = tmp_path / "output.py"
    with patch("builtins.input", return_value="n"):
        _run_dry(arguments(source, destination))
    assert not destination.exists()
    assert source.read_text() == "x = 1\n"
