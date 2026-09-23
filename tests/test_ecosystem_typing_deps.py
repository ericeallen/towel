"""A corpus project's environment holds what the project declares its own type check needs.

A project checks its code against more than its runtime dependencies: stub packages,
plugins, the test framework its tests are typed against, the tools a noxfile
imports. Without them its own type check reports errors the project never sees, and
Towel declines the typed path over them (packaging's noxfile imports nox, which only
its pre-commit mypy hook installs). The harness reads these requirements where
projects declare them and installs them, adding to the environment without changing
anything already in it. These tests pin where it looks, how it reads each format, and
what it records.
"""

from __future__ import annotations

from pathlib import Path
import textwrap
from typing import FrozenSet, List, Mapping, Optional, Tuple

import pytest

from scripts import ecosystem_check as ecosystem
from tests.ecosystem_fixtures import candidate_wheel, project_tree, uv_required, wheel


def _tree(root: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return root


def _found(
    root: Path, files: Mapping[str, str], project: Optional[str] = "sample"
) -> List[Tuple[str, str]]:
    tree = _tree(root, files)
    return [
        (declaration.requirement, declaration.source)
        for declaration in ecosystem.typing_declarations(tree, project)
    ]


# -- Where projects declare them ------------------------------------------------------


def test_dependency_groups_and_extras_named_for_typing_are_read(tmp_path: Path) -> None:
    found = _found(
        tmp_path,
        {
            "pyproject.toml": """
                [project]
                name = "sample"
                version = "1.0"
                [project.optional-dependencies]
                typing = ["types-toml"]
                docs = ["sphinx"]
                [dependency-groups]
                type-check = ["types-requests", {include-group = "base"}, "sample[typing]"]
                base = ["attrs"]
                docs = ["furo"]
            """,
        },
    )
    group = "pyproject.toml [dependency-groups] type-check"
    assert found == [
        ("types-requests", group),
        ("attrs", group),
        # The project naming itself reaches its own extras; it is never installed by name.
        ("sample[typing]", group),
        ("types-toml", f"{group}, through sample[typing]"),
        ("types-toml", "pyproject.toml [project.optional-dependencies] typing"),
    ]


def test_tox_environments_that_run_a_checker_are_read_with_their_factors(tmp_path: Path) -> None:
    found = _found(
        tmp_path,
        {
            "pyproject.toml": """
                [project]
                name = "sample"
                [dependency-groups]
                tests = ["hypothesis"]
                stubs = ["types-six"]
            """,
            "requirements/typing-extra.txt": "types-pytz\n",
            "tox.ini": """
                [tox]
                env_list = py312-{tests,mypy}, lint

                [testenv]
                deps =
                    pytest
                    mypy: pytest-mypy-plugins
                    tests: pytest-xdist
                dependency_groups =
                    tests: tests
                    mypy: stubs
                commands =
                    tests: pytest {posargs}
                    mypy: mypy src

                [testenv:lint]
                deps = flake8
                commands = flake8 src

                [testenv:pyright]
                deps =
                    {[testenv]deps}
                    -r {toxinidir}/requirements/typing-extra.txt
                commands = pyright
            """,
        },
    )
    assert found == [
        # The base environment's mypy factor takes its own lines and none of the tests'.
        ("pytest", "tox.ini [testenv]"),
        ("pytest-mypy-plugins", "tox.ini [testenv]"),
        ("types-six", "tox.ini [testenv], group stubs"),
        # The substituted base deps are read for this environment, whose factors
        # take none of the conditioned ones; the -r file is read in full.
        ("pytest", "tox.ini [testenv:pyright]"),
        ("types-pytz", "tox.ini [testenv:pyright], requirements/typing-extra.txt"),
        # A requirements file named for typing is read on its own account too.
        ("types-pytz", "requirements/typing-extra.txt"),
    ]


def test_tox_toml_environments_that_run_a_checker_are_read(tmp_path: Path) -> None:
    found = _found(
        tmp_path,
        {
            "pyproject.toml": """
                [project]
                name = "sample"
                [dependency-groups]
                stubs = ["types-docutils"]
                [tool.tox.env.check]
                dependency_groups = ["stubs"]
                deps = ["types-pyyaml"]
                commands = [["mypy", "--strict", "src"]]
                [tool.tox.env.tests]
                deps = ["pytest"]
                commands = [["pytest"]]
            """,
        },
    )
    source = "pyproject.toml [tool.tox] env check"
    assert found == [
        ("types-pyyaml", source),
        ("types-docutils", f"{source}, group stubs"),
    ]


def test_nox_sessions_that_run_a_checker_are_read_from_their_syntax(tmp_path: Path) -> None:
    found = _found(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "sample"\n[dependency-groups]\nextra = ["types-mock"]\n',
            "noxfile.py": """
                import nox

                TYPING = ["types-six", "types-pytz"]

                @nox.session(python="3.12")
                def typing(session: nox.Session) -> None:
                    session.install("-e.", "types-attrs", *TYPING, "--group=extra")
                    session.run("mypy", "src")

                @nox.session
                def tests(session):
                    session.install("pytest", "hypothesis")
                    session.run("pytest")
            """,
        },
    )
    source = "noxfile.py session typing"
    assert found == [
        ("types-attrs", source),
        ("types-six", source),
        ("types-pytz", source),
        ("types-mock", f"{source}, group extra"),
    ]


PRE_COMMIT = """
repos:
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.2  # frozen: v1.11.2
    hooks:
      - id: mypy
        args: [--strict]
        additional_dependencies: &type_deps
          - hypothesis
          - 'nox>=2026.8.17'   # the noxfile is checked too
          - "typing_extensions>=4.16.0"
  - repo: https://github.com/RobertCraigie/pyright-python
    rev: v1.1.400
    hooks:
      - id: pyright
        additional_dependencies: [pytest, 'types-requests>=2, <3', "foo#bar"]
  - repo: local
    hooks:
      - id: check-frozen-revs
        additional_dependencies: ['ruamel.yaml']
      - id: mypy
        name: mypy again
        additional_dependencies: *type_deps
"""


def test_pre_commit_checker_hooks_are_read_in_the_yaml_they_are_written_in() -> None:
    assert ecosystem.pre_commit_dependencies(PRE_COMMIT) == [
        ("hook mypy", ["hypothesis", "nox>=2026.8.17", "typing_extensions>=4.16.0"]),
        ("hook pyright", ["pytest", "types-requests>=2, <3", "foo#bar"]),
        # An alias names the same list; a hook that runs no checker is not read.
        ("hook mypy", ["hypothesis", "nox>=2026.8.17", "typing_extensions>=4.16.0"]),
    ]


def test_requirements_files_named_for_typing_are_read_with_their_includes(tmp_path: Path) -> None:
    found = _found(
        tmp_path,
        {
            "requirements-typing.txt": """
                # stubs for the checked code
                -r requirements/base.txt
                types-six==1.16.0 --hash=sha256:abc
                -e .
            """,
            "requirements/base.txt": "attrs\n",
            "requirements/lint.in": "ruff\n",
            "requirements/lint.txt": "ruff==0.16.0\n",
            "test-requirements.txt": "pytest\n",
        },
    )
    assert found == [
        ("ruff==0.16.0", "requirements/lint.txt"),
        ("attrs", "requirements-typing.txt"),
        ("types-six==1.16.0", "requirements-typing.txt"),
    ]


def test_a_project_that_declares_nothing_for_typing_gives_nothing(tmp_path: Path) -> None:
    assert (
        _found(
            tmp_path,
            {
                "pyproject.toml": '[project]\nname = "sample"\n[dependency-groups]\ndocs = ["furo"]\n',
                "tox.ini": "[testenv]\ndeps = pytest\ncommands = pytest\n",
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "condition,factors,applies",
    [
        (None, frozenset(), True),
        ("mypy", frozenset({"mypy"}), True),
        ("tests", frozenset({"mypy"}), False),
        ("py312-mypy", frozenset({"py312", "mypy"}), True),
        ("py312-mypy", frozenset({"mypy"}), False),
        ("tests,mypy", frozenset({"mypy"}), True),
        ("!mypy", frozenset({"mypy"}), False),
        ("3.1{0-2}-mypy", frozenset({"3.11", "mypy"}), True),
        ("{tests,lint}", frozenset({"mypy"}), False),
    ],
)
def test_tox_factor_conditions_decide_which_lines_apply(
    condition: Optional[str], factors: FrozenSet[str], applies: bool
) -> None:
    assert ecosystem._applies(condition, factors) is applies


# -- What is installed, and what is recorded -------------------------------------------


UV_LOCK = """\
version = 1

[[package]]
name = "types-fake"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
"""


@uv_required
def test_typing_dependencies_are_installed_at_the_lock_pin_adding_only(
    tmp_path: Path, offline_index: Path
) -> None:
    """Every declared requirement is accounted for, and nothing already there changes."""
    wheel(offline_index, "types-fake", "1.0.0", {"types_fake/__init__.py": ""})
    wheel(offline_index, "types-fake", "2.0.0", {"types_fake/__init__.py": ""})
    wheel(offline_index, "plugin-base", "1.0.0", {"plugin_base/__init__.py": ""})
    wheel(offline_index, "plugin-base", "2.0.0", {"plugin_base/__init__.py": ""})
    wheel(
        offline_index,
        "needs-new-base",
        "1.0.0",
        {"needs_new_base/__init__.py": ""},
        requires=["plugin-base>=2"],
    )
    candidate = ecosystem.load_candidate(candidate_wheel(tmp_path / "dist"))
    work = tmp_path / "work"
    source = project_tree(work / "sample", "sample", "sample", "source")
    with (source / "pyproject.toml").open("a") as pyproject:
        pyproject.write(textwrap.dedent("""
                [project.optional-dependencies]
                typing = ["types-fake"]
                [dependency-groups]
                typing = [
                    "sample[typing]",
                    "pytest",
                    "mypy",
                    "needs-new-base",
                    "old-only; python_version < '3.0'",
                ]
                """))
    (source / "uv.lock").write_text(UV_LOCK)
    # An installer's own reading of pyproject.toml refuses a malformed group, so the
    # malformed requirement lives where only this harness reads it.
    (source / "requirements-mypy.txt").write_text("not a requirement!\n")
    project = ecosystem.Project(
        "sample", "unused", "pinned", "sample", deps=("plugin-base==1.0.0",)
    )
    installed = ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    group = "pyproject.toml [dependency-groups] typing"
    typing = installed.record.typing
    refused = typing.requirements[4].skipped
    assert typing.requirements == (
        ecosystem.TypingRequirement(
            "sample[typing]", group, skipped="the project itself, installed from its tree"
        ),
        ecosystem.TypingRequirement(
            "types-fake", f"{group}, through sample[typing]", "types-fake==1.0.0", "uv.lock"
        ),
        ecosystem.TypingRequirement("pytest", group, skipped="already installed at pytest 0.0.1"),
        ecosystem.TypingRequirement(
            "mypy", group, skipped="Towel's tool selection holds mypy 2.0.0"
        ),
        ecosystem.TypingRequirement("needs-new-base", group, "needs-new-base", skipped=refused),
        ecosystem.TypingRequirement(
            "old-only; python_version < '3.0'",
            group,
            skipped="its marker excludes this environment",
        ),
        ecosystem.TypingRequirement(
            "types-fake",
            "pyproject.toml [project.optional-dependencies] typing",
            "types-fake==1.0.0",
            "uv.lock",
        ),
        ecosystem.TypingRequirement(
            "not a requirement!",
            "requirements-mypy.txt",
            skipped="not a requirement an installer takes",
        ),
    )
    # needs-new-base wants plugin-base 2, and the environment keeps the 1.0.0 it has.
    assert refused.startswith("the installer refused it:")
    assert typing.added == ("types-fake==1.0.0",)
    assert typing.removed == ()
    versions = ecosystem.probe_environment(installed.python, ["plugin-base", "types-fake"]).versions
    assert versions == {"plugin-base": "1.0.0", "types-fake": "1.0.0"}
    # A reused environment keeps the record it was built with.
    again = ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    assert again.record.typing == typing


def test_the_summary_says_how_much_of_what_was_declared_was_installed() -> None:
    typing = ecosystem.TypingDependencies(
        (
            ecosystem.TypingRequirement("nox", ".pre-commit-config.yaml hook mypy", "nox"),
            ecosystem.TypingRequirement(
                "pytest", ".pre-commit-config.yaml hook mypy", skipped="already installed"
            ),
            ecosystem.TypingRequirement("types-x", "tox.ini [testenv:mypy]", "types-x==1"),
        ),
        ("nox==2026.9.1", "types-x==1"),
    )
    assert (
        ecosystem._typing_cell(typing)
        == "2 of 3 (.pre-commit-config.yaml hook mypy; tox.ini [testenv:mypy])"
    )
    assert ecosystem._typing_cell(ecosystem.TypingDependencies()) == "none declared"
