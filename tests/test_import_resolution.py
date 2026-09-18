"""A base-class name resolves through the referencing module's own imports."""

from __future__ import annotations

from pathlib import Path

from towel.unification.import_graph import imported_definition_sites
from towel.unification.import_graph import ImportGraphCache


def _project(root: Path) -> Path:
    (root / "pyproject.toml").write_text('[project]\nname="proj"\nversion="0"\n')
    pkg = root / "proj"
    for directory in (pkg, pkg / "one", pkg / "two"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("")
    (pkg / "one" / "base.py").write_text("class Base:\n    class Inner:\n        pass\n")
    (pkg / "two" / "base.py").write_text("class Base:\n    pass\n")
    return pkg


def _sites(pkg: Path, source: str) -> set[tuple[str, str]]:
    module = pkg / "one" / "user.py"
    module.write_text(source)
    resolved = imported_definition_sites(str(module), "Base", ImportGraphCache())
    assert resolved is not None
    return {(str(path.relative_to(pkg)), qualname) for path, qualname in resolved}


def test_relative_from_import_names_the_sibling_module(tmp_path: Path) -> None:
    pkg = _project(tmp_path)
    assert _sites(pkg, "from .base import Base\n") == {("one/base.py", "Base")}


def test_absolute_from_import_reaches_the_other_package(tmp_path: Path) -> None:
    pkg = _project(tmp_path)
    assert _sites(pkg, "from proj.two.base import Base\n") == {("two/base.py", "Base")}


def test_alias_maps_back_to_the_original_name(tmp_path: Path) -> None:
    pkg = _project(tmp_path)
    assert _sites(
        pkg, "from proj.two.base import Base as Base\nfrom .base import Other as Base\n"
    ) == {("one/base.py", "Other")}


def test_dotted_reference_through_a_module_import(tmp_path: Path) -> None:
    pkg = _project(tmp_path)
    module = pkg / "one" / "user.py"
    module.write_text("import proj.two.base\nfrom . import base\n")
    dotted = imported_definition_sites(str(module), "proj.two.base.Base", ImportGraphCache())
    nested = imported_definition_sites(str(module), "base.Base.Inner", ImportGraphCache())
    assert dotted is not None and {(p.name, q) for p, q in dotted} >= {("base.py", "Base")}
    assert nested is not None and ("Base.Inner" in {q for _, q in nested})
    assert all(p.parent.name == "one" for p, q in nested if q == "Base.Inner")


def test_names_without_an_unconditional_import_resolve_to_nothing(tmp_path: Path) -> None:
    pkg = _project(tmp_path)
    module = pkg / "one" / "user.py"
    module.write_text(
        "try:\n    from .base import Base\nexcept ImportError:\n    Base = object\n"
        "from .other import *\n"
    )
    assert imported_definition_sites(str(module), "Base", ImportGraphCache()) is None
    assert imported_definition_sites(str(module), "Unbound", ImportGraphCache()) is None
