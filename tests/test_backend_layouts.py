"""Package paths corroborated against Hatch and setuptools wheel contents."""

from pathlib import Path

import pytest

from towel.unification.project_layout import ProjectLayout


def package(root: Path, relative: str) -> Path:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text("")
    module = directory / "tools.py"
    module.write_text("VALUE = 7\n")
    return module


def hatch(root: Path, options: str = "", name: str = "my-project") -> None:
    (root / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="hatchling.build"\n'
        f'[project]\nname="{name}"\nversion="0.0.0"\n{options}'
    )


def test_hatch_default_matches_built_wheel_package_name(tmp_path):
    hatch(tmp_path)
    module = package(tmp_path, "src/my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_hatch_flat_package_precedes_src_default(tmp_path):
    hatch(tmp_path)
    flat = package(tmp_path, "my_project")
    package(tmp_path, "src/my_project")
    assert ProjectLayout.discover(flat).source_roots == [tmp_path.resolve()]


@pytest.mark.parametrize("section", ["tool.hatch.build", "tool.hatch.build.targets.wheel"])
def test_hatch_explicit_wheel_packages_strip_parent_directory(tmp_path, section):
    hatch(tmp_path, f'[{section}]\npackages=["library/renamed"]\n')
    module = package(tmp_path, "library/renamed")
    assert ProjectLayout.discover(module).module_name_for(module) == "renamed.tools"


def test_hatch_wheel_selection_overrides_global_packages(tmp_path):
    hatch(
        tmp_path,
        '[tool.hatch.build]\npackages=["old/first"]\n'
        '[tool.hatch.build.targets.wheel]\npackages=["new/second"]\n',
    )
    package(tmp_path, "old/first")
    module = package(tmp_path, "new/second")
    assert ProjectLayout.discover(module).module_name_for(module) == "second.tools"


@pytest.mark.parametrize(
    "option",
    ['sources={"src"="other"}', 'only-include=["src"]', 'packages=["src/*"]', "packages=[]"],
)
def test_custom_hatch_layout_fails_explicitly(tmp_path, option):
    hatch(tmp_path, f"[tool.hatch.build.targets.wheel]\n{option}\n")
    module = package(tmp_path, "src/my_project")
    with pytest.raises(ValueError, match="Hatch"):
        ProjectLayout.discover(module)


def test_separate_hatch_configuration_is_not_ignored(tmp_path):
    hatch(tmp_path)
    (tmp_path / "hatch.toml").write_text('[build]\npackages=["other"]\n')
    with pytest.raises(ValueError, match="hatch.toml"):
        ProjectLayout.discover(package(tmp_path, "src/my_project"))


def test_named_setuptools_mapping_keeps_logical_package_prefix(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools]\npackages=["actual_name"]\npackage-dir={actual_name="lib"}\n'
    )
    module = package(tmp_path, "lib")
    layout = ProjectLayout.discover(module)
    assert layout.module_name_for(module) == "actual_name.tools"
    assert layout.module_name_for(module.parent / "__init__.py") == "actual_name"


def test_specific_named_mapping_takes_precedence_over_default_root(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools]\npackage-dir={""="src", "actual_name"="src/physical"}\n'
    )
    module = package(tmp_path, "src/physical")
    assert ProjectLayout.discover(module).module_name_for(module) == "actual_name.tools"


@pytest.mark.parametrize("backend", ["poetry.core.masonry.api", "custom.backend"])
def test_unknown_backend_rejects_even_incidental_setuptools_configuration(tmp_path, backend):
    (tmp_path / "pyproject.toml").write_text(
        f'[build-system]\nbuild-backend="{backend}"\n'
        '[project]\nname="my-project"\nversion="0.0.0"\n'
        '[tool.setuptools]\npackage-dir={""="src"}\n'
    )
    with pytest.raises(ValueError, match="Unsupported build backend"):
        ProjectLayout.discover(package(tmp_path, "src/my_project"))


def test_hatch_unmatched_name_does_not_guess_src_as_package(tmp_path):
    hatch(tmp_path, name="different-name")
    with pytest.raises(ValueError, match="Unsupported Hatch default package layout"):
        ProjectLayout.discover(package(tmp_path, "src/my_project"))


def test_hatch_ignores_setuptools_configuration(tmp_path):
    hatch(tmp_path, '[tool.setuptools]\npackage-dir={""="incorrect"}\n')
    (tmp_path / "incorrect").mkdir()
    module = package(tmp_path, "src/my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_hatch_requires_name_for_implicit_layout(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[build-system]\nbuild-backend="hatchling.build"\n')
    with pytest.raises(ValueError, match="Hatch project name"):
        ProjectLayout.discover(package(tmp_path, "src/my_project"))


@pytest.mark.parametrize("packaged", [False, True])
def test_vcs_root_does_not_control_crossfile_imports(tmp_path, packaged):
    import subprocess
    import sys
    from towel.changes import apply_changes
    from towel.unification.refactor_engine import UnificationRefactorEngine

    (tmp_path / ".git").mkdir()
    target = tmp_path / "consumer"
    target.mkdir()
    if packaged:
        (target / "__init__.py").write_text("")
    body = "    y=x+1\n    z=y*2\n    return z\n"
    a, b = target / "a.py", target / "b.py"
    a.write_text("def first(x):\n" + body)
    b.write_text("def second(x):\n" + body)
    imports = (
        "from consumer.a import first; from consumer.b import second"
        if packaged
        else "from a import first; from b import second"
    )
    command = [sys.executable, "-c", imports + "; print(first(2),second(2))"]
    cwd = tmp_path if packaged else target
    before = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    engine = UnificationRefactorEngine(min_lines=2)
    proposal = engine.analyze_files([str(a), str(b)], progress="none")[0]
    apply_changes(engine.plan_refactoring(proposal))
    after = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    assert before.stdout == after.stdout == "6 6\n"


def test_flit_module_in_project_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend = "flit_core.buildapi"\n[project]\nname = "mdit-lib"\n'
    )
    (tmp_path / "mdit_lib").mkdir()
    (tmp_path / "mdit_lib" / "__init__.py").write_text("")
    layout = ProjectLayout.discover(tmp_path / "mdit_lib" / "__init__.py")
    assert layout.source_roots == [tmp_path.resolve()]


def test_flit_explicit_module_under_src(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend = "flit_core.buildapi"\n[project]\nname = "anything"\n'
        '[tool.flit.module]\nname = "core"\n'
    )
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "core" / "__init__.py").write_text("")
    layout = ProjectLayout.discover(tmp_path / "src" / "core" / "__init__.py")
    assert layout.source_roots == [(tmp_path / "src").resolve()]


def test_flit_missing_module_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend = "flit_core.buildapi"\n[project]\nname = "ghost"\n'
    )
    (tmp_path / "other.py").write_text("")
    with pytest.raises(ValueError, match="Flit module was not found"):
        ProjectLayout.discover(tmp_path / "other.py")


def test_hatch_sources_directories_are_import_roots(tmp_path):
    hatch(tmp_path, '[tool.hatch.build.targets.wheel]\nsources=["src"]\nonly-include=["src"]\n')
    module = package(tmp_path, "src/black")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "src").resolve()]
    assert layout.module_name_for(module) == "black.tools"


@pytest.mark.parametrize(
    "options",
    [
        'sources=["src"]\nonly-include=["other"]\n',
        'sources=["src/*"]\n',
        'sources=["missing"]\n',
    ],
)
def test_hatch_sources_that_cannot_name_roots_fail_explicitly(tmp_path, options):
    hatch(tmp_path, f"[tool.hatch.build.targets.wheel]\n{options}")
    module = package(tmp_path, "src/black")
    with pytest.raises(ValueError, match="Hatch"):
        ProjectLayout.discover(module)
