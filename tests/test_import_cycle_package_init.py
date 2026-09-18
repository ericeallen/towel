"""``from . import name`` runs the package initializer, so it is an import edge.

A submodule that reaches back into its package this way must not host a
helper the package's ``__init__`` would import: the initializer would import
the submodule, which imports the half-initialized package. The guard used to
resolve ``from . import name`` to no file at all when ``name`` was not a
submodule, so beautifulsoup4's tests package was rewritten into exactly that
cycle.
"""

from __future__ import annotations

from pathlib import Path

from towel.unification.semantic_safety import would_create_import_cycle


def test_relative_import_of_a_package_attribute_is_an_edge_to_the_initializer(
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("class Base:\n    pass\n")
    (package / "sub.py").write_text("from . import Base\n")
    (package / "other.py").write_text("x = 1\n")
    # Hosting a helper in ``sub`` for a site in ``__init__`` closes the cycle.
    assert would_create_import_cycle(str(package / "sub.py"), {str(package / "__init__.py")})
    # The initializer itself is a safe host: ``sub`` already imports it.
    assert not would_create_import_cycle(str(package / "__init__.py"), {str(package / "sub.py")})
    assert not would_create_import_cycle(str(package / "other.py"), {str(package / "sub.py")})
