"""Wheels, projects and an offline index for the tests of the corpus harness's environments.

Every requirement comes from a directory of fixture wheels and ``uv`` never reaches an
index, so the environment code runs for real without the network.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import shutil
from typing import Dict, Mapping, Optional, Sequence
import zipfile

import pytest

CLI = """\
import sys


def main():
    if sys.argv[1:] == ["dry", "--help"]:
        print("usage: towel dry [-h] INPUT OUTPUT")
        return 0
    return 2
"""
TYPES = ('mypy>=1.0; extra == "types"', 'pyright>=1.1; extra == "types"')
FORMAT = (
    'black>=26.3.1; extra == "format"',
    'isort>=5.12; extra == "format"',
    'ruff>=0.4; extra == "format"',
)


def record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def wheel(
    directory: Path,
    name: str,
    version: str,
    files: Mapping[str, str],
    *,
    requires: Sequence[str] = (),
    scripts: Optional[Mapping[str, str]] = None,
) -> Path:
    """A pure-Python wheel holding ``files``, as an installer expects one."""
    stem = name.replace("-", "_")
    info = f"{stem}-{version}.dist-info"
    contents = dict(files)
    contents[f"{info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
    )
    contents[f"{info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    if scripts:
        contents[f"{info}/entry_points.txt"] = "[console_scripts]\n" + "".join(
            f"{script} = {target}\n" for script, target in scripts.items()
        )
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"{stem}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        lines = []
        for path, text in contents.items():
            archive.writestr(path, text.encode())
            lines.append(record_line(path, text.encode()))
        archive.writestr(f"{info}/RECORD", "\n".join([*lines, f"{info}/RECORD,,"]) + "\n")
    return wheel


def towel_files() -> Dict[str, str]:
    return {
        "towel/__init__.py": '"""A stand-in for the candidate."""\n',
        "towel/cli.py": CLI,
    }


def candidate_wheel(directory: Path, *, requires: Sequence[str] = (*TYPES, *FORMAT)) -> Path:
    return wheel(
        directory,
        "code-towel",
        "9.9.9",
        towel_files(),
        requires=[*requires, 'pytest>=9; extra == "dev"'],
        scripts={"towel": "towel.cli:main"},
    )


def source_tree(root: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


BACKEND = '''\
"""A build backend with no requirements, so an editable install needs no index."""

import base64
import hashlib
import os
import tomllib
import zipfile


def _record(path, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def get_requires_for_build_editable(config_settings=None):
    return []


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    with open("pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    name, version = project["name"], project["version"]
    stem = name.replace("-", "_")
    info = f"{stem}-{version}.dist-info"
    files = {
        f"_{stem}_editable.pth": os.path.abspath(os.getcwd()) + "\\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\\nName: {name}\\nVersion: {version}\\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\n"
        "Tag: py3-none-any\\n",
    }
    wheel = f"{stem}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(os.path.join(wheel_directory, wheel), "w") as archive:
        lines = []
        for path, text in files.items():
            archive.writestr(path, text.encode())
            lines.append(_record(path, text.encode()))
        archive.writestr(f"{info}/RECORD", "\\n".join([*lines, f"{info}/RECORD,,"]) + "\\n")
    return wheel


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    raise NotImplementedError("this fixture only installs editable")
'''

uv_required = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")


def project_tree(root: Path, name: str, package: str, tree: str) -> Path:
    return source_tree(
        root,
        {
            "pyproject.toml": (
                '[build-system]\nrequires = []\nbuild-backend = "backend"\n'
                f'backend-path = ["."]\n\n[project]\nname = "{name}"\nversion = "1.0"\n'
            ),
            "backend.py": BACKEND,
            f"{package}/__init__.py": f"TREE = {tree!r}\n",
        },
    )


def packaging_wheel(directory: Path) -> Path:
    """A wheel of the ``packaging`` running these tests: the real one, from no index."""
    import packaging

    root = Path(packaging.__file__).parent
    files = {
        f"packaging/{path.relative_to(root).as_posix()}": path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    }
    return wheel(directory, "packaging", packaging.__version__, files)


def offline_index_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """uv with no index: every requirement comes from a directory of fixture wheels."""
    links = tmp_path / "links"
    # pytest brings packaging, which answers the harness's questions about versions.
    wheel(links, "pytest", "0.0.1", {"pytest.py": ""}, requires=["packaging"])
    packaging_wheel(links)
    # Two of each tool that a lock pins below, so a pin is distinguishable from what
    # resolves today; Black's older one also fails the format extra's requirement.
    wheel(links, "mypy", "1.0.0", {"mypy/__init__.py": ""})
    wheel(links, "mypy", "2.0.0", {"mypy/__init__.py": ""})
    wheel(links, "pyright", "1.1.0", {"pyright/__init__.py": ""})
    wheel(links, "black", "22.12.0", {"black/__init__.py": ""})
    wheel(links, "black", "26.5.1", {"black/__init__.py": ""})
    wheel(links, "isort", "9.0.1", {"isort/__init__.py": ""})
    wheel(links, "ruff", "0.4.0", {"ruff/__init__.py": ""})
    wheel(links, "ruff", "0.16.0", {"ruff/__init__.py": ""})
    for name, value in {
        "UV_OFFLINE": "1",
        "UV_NO_INDEX": "1",
        "UV_FIND_LINKS": str(links),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never",
    }.items():
        monkeypatch.setenv(name, value)
    return links
