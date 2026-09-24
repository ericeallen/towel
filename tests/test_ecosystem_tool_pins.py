"""A corpus project's checkers and formatters are installed at the versions the project pins.

The harness took those versions from a lock file only, but a project pins its checkers
where it runs them too: in ``.pre-commit-config.yaml``, as the revision of its mypy or
pyright hook, and in requirements files (httpx's ``requirements.txt`` has
``mypy==1.17.1``). Such a project was checked by whatever version of mypy resolved that
day. Each source is read now, in a fixed order -- a lock file, then a pre-commit hook's
revision, then a requirements file, then the candidate's extra -- and the record names
the file that chose each version.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap
from typing import Dict, Mapping, Optional, Tuple

import pytest

from scripts import ecosystem_check as ecosystem
from tests.ecosystem_fixtures import candidate_wheel, project_tree, uv_required

TOOLS = ("mypy", "pyright", "black", "isort", "ruff")


def _tree(root: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return root


# -- pre-commit ------------------------------------------------------------------


def test_a_hook_repository_is_pinned_at_the_version_its_revision_names() -> None:
    """Every layout of the configuration the corpus's projects write, and what pins nothing."""
    text = textwrap.dedent("""
        repos:
          - repo: https://github.com/pre-commit/mirrors-mypy
            rev: v1.17.1  # the mypy of CI
            hooks:
              - id: mypy
                additional_dependencies: [types-requests]
          - repo: https://github.com/astral-sh/ruff-pre-commit
            rev: c60c980e561ed3e73101667fe8365c609d19a438  # frozen: v0.15.9
            hooks:
              - id: ruff-check
          - repo: https://github.com/psf/black-pre-commit-mirror
            rev: "23.11.0"
            hooks:
              - id: black
          - rev: 5.12.0
            repo: https://github.com/PyCQA/isort
          - repo: https://github.com/codespell-project/codespell
            rev: 2ccb47ff45ad361a21071a7eedda4c37e6ae8c5a
          - repo: https://github.com/example/branch-hooks
            rev: main
          - repo: local
            hooks:
              - id: pyright
                entry: pyright
        ci:
          autoupdate_schedule: monthly
        """)
    assert ecosystem.pre_commit_versions(text) == [
        ("https://github.com/pre-commit/mirrors-mypy", "1.17.1"),
        ("https://github.com/astral-sh/ruff-pre-commit", "0.15.9"),
        ("https://github.com/psf/black-pre-commit-mirror", "23.11.0"),
        ("https://github.com/PyCQA/isort", "5.12.0"),
    ]
    # Sequences written flush with their key, and a wide dash, as marshmallow's and pluggy's.
    flush = "repos:\n- repo: https://github.com/astral-sh/ruff-pre-commit\n  rev: v0.16.6\n"
    wide = "repos:\n-   repo: https://github.com/pre-commit/mirrors-mypy\n    rev: v2.3.1\n"
    assert ecosystem.pre_commit_versions(flush) == [
        ("https://github.com/astral-sh/ruff-pre-commit", "0.16.6")
    ]
    assert ecosystem.pre_commit_versions(wide) == [
        ("https://github.com/pre-commit/mirrors-mypy", "2.3.1")
    ]


def test_a_hook_repository_pins_the_tool_it_runs(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        {
            ".pre-commit-config.yaml": """
                repos:
                  - repo: https://github.com/RobertCraigie/pyright-python.git
                    rev: v1.1.408
                  - repo: git@github.com:pre-commit/mirrors-mypy
                    rev: v1.17.1
                  - repo: https://github.com/pycqa/isort
                    rev: 9.0.1
                  - repo: https://github.com/charliermarsh/ruff-pre-commit
                    rev: v0.4.0
                  - repo: https://github.com/astral-sh/ruff-pre-commit
                    rev: v0.16.7
                  - repo: https://github.com/asottile/blacken-docs
                    rev: 1.20.0
            """,
        },
    )
    assert ecosystem.pre_commit_pins(tree, TOOLS) == {
        "pyright": "1.1.408",
        "mypy": "1.17.1",
        "isort": "9.0.1",
        # The first repository running a tool decides.
        "ruff": "0.4.0",
    }
    assert ecosystem.pre_commit_pins(tree, ["mypy"]) == {"mypy": "1.17.1"}
    assert ecosystem.pre_commit_pins(tmp_path / "absent", TOOLS) == {}


# -- Requirements files ----------------------------------------------------------


def test_a_requirements_file_pins_only_exactly_and_only_for_this_interpreter(
    tmp_path: Path,
) -> None:
    tree = _tree(
        tmp_path,
        {
            # httpx's, and what trio's compiled test requirements pin.
            "requirements.txt": """
                -r ci/tools.txt
                mypy==1.17.1
                ruff>=0.4
                black==22.12.0 ; python_version < "3"
                isort===5.12.0 --hash=sha256:0123
            """,
            # No requirements file of its own, so its pins are the file's that includes it.
            "ci/tools.txt": "pyright==1.1.408\n",
            # Named for typing, so read before the rest.
            "requirements/lint.txt": "mypy==1.0.0\n",
            # Compiled into the .txt beside it, which is what is read.
            "requirements/docs.in": "black==99.0\n",
            "requirements/docs.txt": "black==26.5.1\n",
            "requirements/tests.txt": "ruff==0.16.7\n",
        },
    )
    assert ecosystem.requirement_pins(Path(sys.executable), tree, TOOLS) == {
        "mypy": ("requirements/lint.txt", "1.0.0"),
        "black": ("requirements/docs.txt", "26.5.1"),
        "ruff": ("requirements/tests.txt", "0.16.7"),
        "isort": ("requirements.txt", "5.12.0"),
        "pyright": ("requirements.txt", "1.1.408"),
    }
    assert ecosystem.requirement_pins(Path(sys.executable), tree, ["pyright"]) == {
        "pyright": ("requirements.txt", "1.1.408")
    }


# -- The order of the sources -----------------------------------------------------


def test_a_lock_file_decides_then_pre_commit_then_a_requirements_file(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        {
            "uv.lock": '[[package]]\nname = "mypy"\nversion = "1.0.0"\n',
            ".pre-commit-config.yaml": """
                repos:
                  - repo: https://github.com/pre-commit/mirrors-mypy
                    rev: v2.0.0
                  - repo: https://github.com/astral-sh/ruff-pre-commit
                    rev: v0.4.0
            """,
            "requirements.txt": "mypy==3.0\nruff==0.5.0\nisort==9.0.1\n",
        },
    )
    assert ecosystem.tool_pins(Path(sys.executable), tree, TOOLS) == {
        "mypy": ("uv.lock", "1.0.0"),
        "ruff": (".pre-commit-config.yaml", "0.4.0"),
        "isort": ("requirements.txt", "9.0.1"),
    }


@pytest.mark.parametrize(
    "recorded,source",
    [
        (".pre-commit-config.yaml", ".pre-commit-config.yaml"),
        ("requirements.txt", "requirements.txt"),
        ("requirements/lint.in", "requirements/lint.in"),
        ("uv.lock", "uv.lock"),
        ("towel[types]", "towel[types]"),
        ("/etc/requirements.txt", None),
        ("../requirements.txt", None),
        ("setup.cfg", None),
    ],
)
def test_a_recorded_choice_is_read_back_only_as_a_source_it_can_be(
    tmp_path: Path, recorded: str, source: Optional[str]
) -> None:
    """A reused environment's record is trusted only as far as it names a real source.

    One it cannot read makes the environment be built again, rather than reported wrong.
    """
    path = tmp_path / "towel-tools.json"
    path.write_text(json.dumps({"mypy": {"source": recorded, "overridden": ""}}))
    choices = ecosystem._read_provenance(path)
    assert (None if choices is None else choices["mypy"].source) == source


def test_the_summary_names_the_file_that_chose_a_version() -> None:
    tools = (
        ecosystem.Tool("mypy", "1.17.1", ecosystem.RequirementsFile("requirements.txt")),
        ecosystem.Tool("pyright", "1.1.408", ".pre-commit-config.yaml"),
        ecosystem.Tool("black", "26.5.1", "towel[format]", ".pre-commit-config.yaml 22.12.0"),
        ecosystem.Tool("ruff", "0.16.0", "towel[format]"),
    )
    assert ecosystem._tools_cell(tools) == (
        "mypy 1.17.1 (requirements.txt), pyright 1.1.408 (.pre-commit-config.yaml), "
        "black 26.5.1 (over .pre-commit-config.yaml 22.12.0), ruff 0.16.0"
    )


# -- The environment, built for real ------------------------------------------------


@uv_required
def test_a_tool_pinned_by_pre_commit_or_a_requirements_file_is_installed_at_its_pin(
    tmp_path: Path, offline_index: Path
) -> None:
    candidate = ecosystem.load_candidate(candidate_wheel(tmp_path / "dist"))
    work = tmp_path / "work"
    source = project_tree(work / "sample", "sample", "sample", "source")
    _tree(
        source,
        {
            ".pre-commit-config.yaml": """
                repos:
                  - repo: https://github.com/pre-commit/mirrors-mypy
                    rev: v1.0.0
                  - repo: https://github.com/psf/black-pre-commit-mirror
                    rev: 22.12.0
            """,
            # mypy's pin loses to the hook's; ruff's is the only one there is.
            "requirements.txt": "mypy==2.0.0\nruff==0.4.0\n",
        },
    )
    project = ecosystem.Project("sample", "unused", "pinned", "sample")
    installed = ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    expected: Dict[str, Tuple[ecosystem.Tool, ...]] = {
        "checkers": (
            ecosystem.Tool("mypy", "1.0.0", ".pre-commit-config.yaml"),
            ecosystem.Tool("pyright", "1.1.0", "towel[types]"),
        ),
        "formatters": (
            # Below the format extra's floor, so the extra's replaces it, as installing does.
            ecosystem.Tool("black", "26.5.1", "towel[format]", ".pre-commit-config.yaml 22.12.0"),
            ecosystem.Tool("isort", "9.0.1", "towel[format]"),
            ecosystem.Tool("ruff", "0.4.0", ecosystem.RequirementsFile("requirements.txt")),
        ),
    }
    assert installed.record.checkers == expected["checkers"]
    assert installed.record.formatters == expected["formatters"]
    # The record survives the environment's reuse: it is what the next run reports.
    marker = work / "sample-env/reused"
    marker.write_text("")
    again = ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    assert marker.exists(), "an environment whose record names these sources was rebuilt"
    assert again.record == installed.record
