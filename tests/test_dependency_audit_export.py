"""The audit inventory retains all third-party packages and fails closed on bad metadata."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_dependencies.py"


def _distribution(root: Path, directory: str, name: str, version: str) -> None:
    metadata = root / f"{directory}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )


def _export(root: Path) -> subprocess.CompletedProcess[str]:
    # -S removes the test runner's environment; PYTHONPATH supplies only this
    # test's metadata. This also proves the exporter needs no third-party code.
    return subprocess.run(
        [sys.executable, "-S", str(SCRIPT)],
        cwd=root,
        env={"PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_inventory_excludes_only_towel_and_keeps_editable_and_transitive_packages(
    tmp_path: Path,
) -> None:
    _distribution(tmp_path, "local", "Code_Towel", "1.732")
    _distribution(tmp_path, "wheel", "code-towel", "1.732")
    _distribution(tmp_path, "editable", "Editable_Dep", "2.0")
    (tmp_path / "editable.dist-info" / "direct_url.json").write_text(
        '{"url":"file:///example","dir_info":{"editable":true}}', encoding="utf-8"
    )
    _distribution(tmp_path, "leaf", "Leaf.Dep", "3.1")
    _distribution(tmp_path, "duplicate_leaf", "leaf-dep", "3.1")
    _distribution(tmp_path, "similar_name", "code-towel-helper", "1.0")
    result = _export(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "code-towel-helper==1.0\neditable-dep==2.0\nleaf-dep==3.1\n"


def test_conflicting_installed_versions_fail_without_a_partial_inventory(tmp_path: Path) -> None:
    _distribution(tmp_path, "first", "dependency", "1.0")
    _distribution(tmp_path, "second", "dependency", "2.0")
    result = _export(tmp_path)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Conflicting installed versions for dependency" in result.stderr


@pytest.mark.parametrize(
    "name, version",
    [("", "1.0"), ("bad name", "1.0"), ("dependency", ""), ("dependency", "1.0 --no-deps")],
)
def test_malformed_metadata_fails_without_a_partial_inventory(
    name: str, version: str, tmp_path: Path
) -> None:
    _distribution(tmp_path, "valid", "valid", "1.0")
    _distribution(tmp_path, "invalid", name, version)
    result = _export(tmp_path)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Cannot collect audit dependencies" in result.stderr


def test_bare_environment_can_export_an_empty_dependency_inventory(tmp_path: Path) -> None:
    _distribution(tmp_path, "project", "code-towel", "1.732")
    result = _export(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
