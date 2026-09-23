"""Package paths corroborated against Hatch and setuptools wheel contents."""

from pathlib import Path

import pytest

from towel.project_layout import ProjectLayout
from towel.unification.exceptions import UnsupportedLayoutError


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


@pytest.mark.parametrize("backend", ["scikit_build_core.build", "custom.backend"])
def test_foreign_backend_ignores_setuptools_config_but_infers_conventional_layout(
    tmp_path, backend
):
    # A foreign backend's incidental [tool.setuptools] table is not trusted, but
    # a conventional src/<name> package is still inferred by name.
    (tmp_path / "pyproject.toml").write_text(
        f'[build-system]\nbuild-backend="{backend}"\n'
        '[project]\nname="my-project"\nversion="0.0.0"\n'
        '[tool.setuptools]\npackage-dir={""="src"}\n'
    )
    module = package(tmp_path, "src/my_project")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "src").resolve()]
    assert layout.module_name_for(module) == "my_project.tools"


@pytest.mark.parametrize("backend", ["scikit_build_core.build", "custom.backend"])
def test_foreign_backend_with_nonconventional_layout_is_refused(tmp_path, backend):
    # The only package sits at a non-conventional location named by setuptools
    # config the foreign backend does not license us to trust, so it is refused.
    (tmp_path / "pyproject.toml").write_text(
        f'[build-system]\nbuild-backend="{backend}"\n'
        '[project]\nname="my-project"\nversion="0.0.0"\n'
        '[tool.setuptools]\npackage-dir={""="weird"}\n'
    )
    with pytest.raises(ValueError, match="Unsupported build backend"):
        ProjectLayout.discover(package(tmp_path, "weird/my_project"))


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


def poetry(root: Path, options: str = "", name: str = "my-project") -> None:
    (root / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="poetry.core.masonry.api"\n'
        f'[tool.poetry]\nname="{name}"\nversion="0.0.0"\n{options}'
    )


def test_poetry_default_package_beside_pyproject(tmp_path: Path) -> None:
    poetry(tmp_path)
    module = package(tmp_path, "my_project")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [tmp_path.resolve()]
    assert layout.module_name_for(module) == "my_project.tools"


def test_poetry_default_package_under_src(tmp_path: Path) -> None:
    poetry(tmp_path)
    module = package(tmp_path, "src/my_project")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "src").resolve()]
    assert layout.module_name_for(module) == "my_project.tools"


def test_poetry_pep621_name_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="poetry.core.masonry.api"\n'
        '[project]\nname="Mixed-Case"\nversion="0.0.0"\n'
    )
    module = package(tmp_path, "mixed_case")
    assert ProjectLayout.discover(module).module_name_for(module) == "mixed_case.tools"


def test_poetry_packages_from_directory_is_the_import_root(tmp_path: Path) -> None:
    poetry(tmp_path, 'packages=[{include="core", from="lib"}]\n')
    module = package(tmp_path, "lib/core")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "lib").resolve()]
    assert layout.module_name_for(module) == "core.tools"


def test_poetry_nested_from_directory_wins_over_the_project_root(tmp_path: Path) -> None:
    poetry(tmp_path, 'packages=[{include="extra"}, {include="core", from="lib"}]\n')
    module = package(tmp_path, "lib/core")
    extra = package(tmp_path, "extra")
    layout = ProjectLayout.discover(module)
    assert set(layout.source_roots) == {tmp_path.resolve(), (tmp_path / "lib").resolve()}
    assert layout.module_name_for(module) == "core.tools"
    assert layout.module_name_for(extra) == "extra.tools"


@pytest.mark.parametrize(
    "options",
    [
        'packages=[{include="core", to="renamed"}]\n',
        'packages=[{include="core/*"}]\n',
        'packages=[{include="missing"}]\n',
        'packages=[{include="../core"}]\n',
        "packages=[]\n",
    ],
)
def test_poetry_layouts_that_cannot_name_roots_fail_explicitly(tmp_path: Path, options) -> None:
    poetry(tmp_path, options)
    module = package(tmp_path, "core")
    with pytest.raises(ValueError, match="Poetry"):
        ProjectLayout.discover(module)


def test_poetry_missing_default_package_is_rejected(tmp_path: Path) -> None:
    poetry(tmp_path, name="ghost")
    module = package(tmp_path, "other")
    with pytest.raises(ValueError, match="Poetry package was not found"):
        ProjectLayout.discover(module)


def test_poetry_ignores_setuptools_configuration(tmp_path: Path) -> None:
    poetry(tmp_path, '[tool.setuptools]\npackage-dir={""="incorrect"}\n')
    module = package(tmp_path, "my_project")
    assert ProjectLayout.discover(module).source_roots == [tmp_path.resolve()]


def pdm(root: Path, options: str = "") -> None:
    (root / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="pdm.backend"\n'
        f'[project]\nname="my-project"\nversion="0.0.0"\n{options}'
    )


def test_pdm_uses_src_when_present(tmp_path: Path) -> None:
    pdm(tmp_path, '[tool.pdm.build]\nincludes=["src/my_project"]\n')
    module = package(tmp_path, "src/my_project")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "src").resolve()]
    assert layout.module_name_for(module) == "my_project.tools"


def test_pdm_flat_layout_without_src(tmp_path: Path) -> None:
    pdm(tmp_path)
    module = package(tmp_path, "my_project")
    assert ProjectLayout.discover(module).source_roots == [tmp_path.resolve()]


def test_pdm_explicit_package_dir(tmp_path: Path) -> None:
    pdm(tmp_path, '[tool.pdm.build]\npackage-dir="lib"\n')
    module = package(tmp_path, "lib/my_project")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "lib").resolve()]
    assert layout.module_name_for(module) == "my_project.tools"


@pytest.mark.parametrize("options", ['package-dir="lib/*"\n', 'package-dir="missing"\n'])
def test_pdm_package_dir_that_cannot_name_a_root_fails_explicitly(tmp_path: Path, options) -> None:
    pdm(tmp_path, f"[tool.pdm.build]\n{options}")
    module = package(tmp_path, "lib/my_project")
    with pytest.raises(ValueError, match="pdm"):
        ProjectLayout.discover(module)


def _generic_backend(root: Path, backend: str, name: str = "my-project", extra: str = "") -> None:
    (root / "pyproject.toml").write_text(
        f'[build-system]\nbuild-backend="{backend}"\n'
        f'[project]\nname="{name}"\nversion="0.0.0"\n{extra}'
    )


def test_unrecognized_backend_infers_conventional_flat_package(tmp_path: Path) -> None:
    # flit_scm is Flit's layout under a different backend string.
    _generic_backend(tmp_path, "flit_scm:buildapi", name="my-project")
    module = package(tmp_path, "my_project")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [tmp_path.resolve()]
    assert layout.module_name_for(module) == "my_project.tools"


def test_unrecognized_backend_infers_conventional_src_package(tmp_path: Path) -> None:
    _generic_backend(tmp_path, "some.exotic.backend", name="Mixed-Case")
    module = package(tmp_path, "src/mixed_case")
    layout = ProjectLayout.discover(module)
    assert layout.source_roots == [(tmp_path / "src").resolve()]
    assert layout.module_name_for(module) == "mixed_case.tools"


def test_unrecognized_backend_infers_single_module(tmp_path: Path) -> None:
    _generic_backend(tmp_path, "flit_scm:buildapi", name="solo")
    (tmp_path / "solo.py").write_text("VALUE = 1\n")
    layout = ProjectLayout.discover(tmp_path / "solo.py")
    assert layout.source_roots == [tmp_path.resolve()]
    assert layout.module_name_for(tmp_path / "solo.py") == "solo"


def test_unrecognized_backend_without_conventional_layout_is_refused(tmp_path: Path) -> None:
    _generic_backend(tmp_path, "weird.backend", name="my-project")
    module = package(tmp_path, "somewhere_else")
    with pytest.raises(ValueError, match="Unsupported build backend"):
        ProjectLayout.discover(module)


def setuptools_find(root: Path, find: str, name: str = "my-project") -> None:
    (root / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="setuptools.build_meta"\n'
        f'[project]\nname="{name}"\nversion="0.0.0"\n'
        f"[tool.setuptools.packages.find]\n{find}"
    )


def test_packages_find_where_names_the_source_root(tmp_path: Path) -> None:
    """The commonest way a setuptools project declares a src layout.

    Nothing read it, and its presence also stopped the conventional ``src``
    inference, which steps aside for explicit configuration. The project root
    was left as the source root and every module gained a component: waitress'
    ``src/waitress/task.py`` became ``src.waitress.task``. A cross-module
    helper imported under that name names a module that does not exist, and
    adopting the reviewed output made the package unimportable -- which is how
    the release corpus found it, waitress being the one project of thirteen
    with this layout that had a cross-module helper to get wrong.
    """
    setuptools_find(tmp_path, 'where = ["src"]\n')
    module = package(tmp_path, "src/my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_a_find_table_without_where_still_means_the_project_root(tmp_path: Path) -> None:
    """``where`` defaults to ``.``, which is what a flat layout wants."""
    setuptools_find(tmp_path, "namespaces = false\n")
    module = package(tmp_path, "my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_several_search_directories_each_name_their_own_modules(tmp_path: Path) -> None:
    """yapf searches ``.`` and ``third_party``; a module is named by the one holding it.

    A layout is discovered for a starting point and keeps the roots that
    contain it, so each module is asked of the layout discovered where it
    lives -- which is what the engine does, discovering from the directory the
    files of one proposal share.
    """
    setuptools_find(tmp_path, 'where = [".", "third_party"]\n')
    own = package(tmp_path, "my_project")
    vendored = package(tmp_path, "third_party/vendored")
    assert ProjectLayout.discover(own).module_name_for(own) == "my_project.tools"
    assert ProjectLayout.discover(vendored).module_name_for(vendored) == "vendored.tools"


def test_a_where_entry_that_is_not_a_plain_directory_is_not_guessed_at(tmp_path: Path) -> None:
    """setuptools does not glob ``where``; a pattern is not a directory to trust."""
    setuptools_find(tmp_path, 'where = ["src*"]\n')
    module = package(tmp_path, "src/my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "src.my_project.tools"


def test_a_foreign_backend_incidental_setuptools_table_is_not_read(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="my-project"\nversion="0.0.0"\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/my_project"]\n'
        '[tool.setuptools.packages.find]\nwhere = ["elsewhere"]\n'
    )
    (tmp_path / "elsewhere").mkdir()
    module = package(tmp_path, "src/my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_hatch_include_selects_files_without_renaming_their_modules(tmp_path: Path) -> None:
    """Corroborated against a built wheel: soupsieve 2.10 at its own pinned commit.

    ``include = ["/soupsieve"]`` produces a wheel holding
    ``soupsieve/css_parser.py`` -- the same path the source tree has, and the
    same module name the project root gives it. ``include`` says which files
    are shipped, not where they land, so refusing it declined two of the
    release corpus's projects for a layout that changes nothing.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="my-project"\nversion="0.0.0"\n'
        '[tool.hatch.build.targets.wheel]\ninclude = ["/my_project"]\n'
    )
    module = package(tmp_path, "my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_hatch_force_include_is_still_refused(tmp_path: Path) -> None:
    """It maps a source path to a different one in the wheel, which can rename a module."""
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="my-project"\nversion="0.0.0"\n'
        '[tool.hatch.build.targets.wheel]\nforce-include = { "vendor" = "my_project/vendor" }\n'
    )
    module = package(tmp_path, "my_project")
    with pytest.raises(UnsupportedLayoutError, match="force-include"):
        ProjectLayout.discover(module).module_name_for(module)


def test_a_hatch_include_names_the_package_when_the_project_name_does_not(
    tmp_path: Path,
) -> None:
    """Corroborated against a built wheel: beautifulsoup4 4.15.0 ships ``bs4``.

    Hatch's own default looks for a directory named after the distribution,
    which a project whose package has a different name defeats. Its wheel
    ``include`` says where the package is -- ``"/bs4/**/*.py"`` -- and with no
    ``sources`` to relocate anything the wheel holds ``bs4/__init__.py`` at its
    root. The directory named before the first wildcard is the package, so the
    directory holding it is the import root.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="distribution-name"\nversion="0.0.0"\n'
        "[tool.hatch.build.targets.wheel]\n"
        'include = ["/my_project/**/*.py", "/my_project/py.typed"]\n'
    )
    module = package(tmp_path, "my_project")
    assert ProjectLayout.discover(module).module_name_for(module) == "my_project.tools"


def test_an_include_naming_no_package_is_no_evidence_and_is_still_refused(
    tmp_path: Path,
) -> None:
    """Refusing remains the answer where nothing says where the package lives."""
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="distribution-name"\nversion="0.0.0"\n'
        '[tool.hatch.build.targets.wheel]\ninclude = ["/docs/**/*.md", "README.rst"]\n'
    )
    (tmp_path / "docs").mkdir()
    module = package(tmp_path, "my_project")
    with pytest.raises(UnsupportedLayoutError, match="default package layout"):
        ProjectLayout.discover(module).module_name_for(module)


@pytest.mark.parametrize(
    "table",
    [
        '[build-system]\nbuild-backend="hatchling.build"\n[project]\nname="p"\n'
        '[tool.hatch.build.targets]\nwheel = "garbage"\n',
        '[build-system]\nbuild-backend="poetry.core.masonry.api"\n[tool]\npoetry = "x"\n',
        '[tool.setuptools]\npackage-dir = "src"\n',
    ],
)
def test_a_layout_table_that_is_not_a_table_is_refused_not_ignored(
    tmp_path: Path, table: str
) -> None:
    """Read as absent, it would apply the backend's defaults to a project that asked otherwise."""
    (tmp_path / "pyproject.toml").write_text(table, encoding="utf-8")
    module = package(tmp_path, "p")
    with pytest.raises(UnsupportedLayoutError, match="is not a table"):
        ProjectLayout.discover(module)
