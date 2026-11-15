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

"""Namespace package overlays retain the module identities used by imports."""

from pathlib import Path

import pytest

pytest.importorskip("mypy")

from mypy.build import BuildSource  # noqa: E402

from towel import _mypy_worker as worker  # noqa: E402
from towel.type_inference import CheckSuccess, MypyInferrer  # noqa: E402


@pytest.mark.parametrize("include_initializer", [False, True])
def test_sparse_namespace_changes_keep_the_verified_baseline(
    tmp_path: Path, include_initializer: bool
) -> None:
    """Usability audit R3: a later namespace edit must not abort a verified preview."""
    files = {
        "pyproject.toml": '[tool.mypy]\nfiles = ["lib/app"]\nfollow_imports = "silent"\n',
        "lib/app/__init__.py": "from namespace.sub import value\nresult: int = value\n",
        "lib/namespace/sub/__init__.py": "from .mod import value\n",
        "lib/namespace/sub/mod.py": "value: int = 1\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    sources = {
        str(tmp_path / name): text
        for name, text in files.items()
        if name.endswith(".py") and (include_initializer or name.startswith("lib/app/"))
    }
    changed = str(tmp_path / "lib/namespace/sub/mod.py")
    oracle = MypyInferrer()
    try:
        assert oracle.check_project(sources) == CheckSuccess(())
        assert oracle.check_project({changed: "value: int = 2\n"}) == CheckSuccess(())
        rejected = oracle.check_project({changed: 'value: str = "broken"\n'})
        assert isinstance(rejected, CheckSuccess), rejected
        assert {error.path for error in rejected.errors} == {str(tmp_path / "lib/app/__init__.py")}
        assert "Incompatible types" in rejected.errors[0].message
        assert oracle.check_project(sources) == CheckSuccess(())
    finally:
        oracle.close()


@pytest.mark.parametrize("reverse_diagnostic", [False, True])
@pytest.mark.parametrize("supplied_initializer", [False, True])
def test_package_and_children_take_one_imported_name(
    tmp_path: Path, reverse_diagnostic: bool, supplied_initializer: bool
) -> None:
    """Usability audit R3: implicit parents and all supplied children move together."""
    package = tmp_path / "namespace/sub"
    initializer = package / "__init__.py"
    sources = [
        BuildSource(str(package / "first.py"), "sub.first", "first", str(package.parent)),
        BuildSource(str(package / "second.py"), "sub.second", "second", str(package.parent)),
    ]
    if supplied_initializer:
        sources.append(BuildSource(str(initializer), "sub", "init", str(package.parent)))
    names = ("namespace.sub", "sub") if reverse_diagnostic else ("sub", "namespace.sub")
    message = (
        f"{initializer}: error: Source file found twice under different module names: "
        f'"{names[0]}" and "{names[1]}"'
    )
    renamed = worker._named_by_its_importer([message], sources, lambda _: False, set())
    assert renamed is not None
    assert [source.module for source in renamed] == [
        "namespace." + source.module for source in sources
    ]
    assert [source.text for source in renamed] == [source.text for source in sources]
    assert {source.base_dir for source in renamed} == {str(tmp_path)}
    assert [source.module for source in sources][:2] == ["sub.first", "sub.second"]


@pytest.mark.parametrize("protected", ["initializer", "child", "renamed"])
def test_a_namespace_repair_does_not_rename_the_projects_targets(
    tmp_path: Path, protected: str
) -> None:
    """Usability audit R3: preserve strict refusals for project targets and repeated aliases."""
    package = tmp_path / "namespace/sub"
    initializer = package / "__init__.py"
    child = package / "mod.py"
    source = BuildSource(str(child), "sub.mod", "text", str(package.parent))
    message = (
        f"{initializer}: error: Source file found twice under different module names: "
        '"namespace.sub" and "sub"'
    )
    judged = {str(initializer)} if protected == "initializer" else {str(child)}
    if protected == "renamed":
        judged = set()
    already_renamed = {str(child)} if protected == "renamed" else set()
    assert (
        worker._named_by_its_importer(
            [message], [source], lambda path: path in judged, already_renamed
        )
        is None
    )
