# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""The documentation site lists every page, and keeps users' pages apart from records.

The user-perspective audit of 1.949 found the guides a user needs mixed into one
folder with five times as many words of design and release records, and no site
to search. The site (zensical.toml, built by `just docs`) is now the reader's path.
Zensical's strict build fails on a broken link or anchor but not on a page left
out of the navigation, so this checks that: a page missing from the navigation
cannot be found by browsing the site. It also checks that the navigation keeps
the user's pages first and the design and maintainers' records in their own
groups at the end, and that headings get the anchors GitHub gives them, which is
what tests/test_documentation_navigation.py checks links against.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import tomllib
from typing import List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = ROOT / "zensical.toml"
USER_GROUPS = ("Getting started", "Guides", "Reference")
CONTRIBUTORS = "Contributors"
RECORD_GROUPS = ("Design", "Maintainers' records")
RECORD_DIRECTORIES = {"Design": "design", "Maintainers' records": "maintainers"}


def _project() -> dict[str, object]:
    project = tomllib.loads(CONFIGURATION.read_text(encoding="utf-8"))["project"]
    assert isinstance(project, dict)
    return project


def _entries(nav: object, group: Tuple[str, ...] = ()) -> List[Tuple[Tuple[str, ...], str]]:
    """Each navigation target with the titles of the groups that hold it."""
    assert isinstance(nav, list), f"navigation under {group} must be a list"
    found: List[Tuple[Tuple[str, ...], str]] = []
    for item in nav:
        if isinstance(item, str):
            found.append((group, item))
            continue
        assert isinstance(item, dict) and len(item) == 1, f"unexpected entry {item!r}"
        ((title, value),) = item.items()
        if isinstance(value, str):
            found.append((group, value))
        else:
            found.extend(_entries(value, (*group, title)))
    return found


def _pages(entries: List[Tuple[Tuple[str, ...], str]]) -> List[str]:
    return [target for _, target in entries if "://" not in target]


def _unlisted(docs: Path, entries: List[Tuple[Tuple[str, ...], str]]) -> List[str]:
    present = sorted(path.relative_to(docs).as_posix() for path in docs.rglob("*.md"))
    return [page for page in present if page not in _pages(entries)]


def test_every_page_under_docs_is_in_the_navigation_once() -> None:
    project = _project()
    docs = ROOT / str(project["docs_dir"])
    entries = _entries(project["nav"])
    pages = _pages(entries)
    assert _unlisted(docs, entries) == []
    assert sorted(set(pages)) == sorted(pages), "a page is listed twice"
    assert [page for page in pages if not (docs / page).is_file()] == []


def test_an_unlisted_page_is_found(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Home\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "page.md").write_text("# Page\n")
    entries = _entries([{"Home": "README.md"}, {"Group": ["other.md"]}])
    assert _unlisted(tmp_path, entries) == ["sub/page.md"]


def _check_grouping(nav: object) -> None:
    assert isinstance(nav, list)
    titles = [next(iter(item)) if isinstance(item, dict) else item for item in nav]
    assert titles == ["Home", *USER_GROUPS, CONTRIBUTORS]
    for group, target in _entries(nav):
        directory = PurePosixPath(target).parts[0] if "://" not in target else ""
        if group[:1] == (CONTRIBUTORS,):
            holder = group[1] if len(group) > 1 else ""
            assert holder in RECORD_DIRECTORIES or "://" in target, f"{target} is loose"
            if holder in RECORD_DIRECTORIES:
                assert directory == RECORD_DIRECTORIES[holder], f"{target} is filed under {holder}"
        else:
            assert directory not in RECORD_DIRECTORIES.values(), f"{target} is in the user's path"


def test_users_pages_come_first_and_records_stay_in_their_own_groups() -> None:
    """Cold reads of 2.0 found 16 proposals and 12 records beside the user's pages.

    The navigation has the user's three groups, then one group for contributors
    that holds the design pages and the maintainers' records, each in its own
    subgroup, so a reader of the guides never browses past them.
    """
    _check_grouping(_project()["nav"])


@pytest.mark.parametrize(
    "nav",
    [
        [{"Home": "README.md"}, {"Guides": ["design/README.md"]}],
        [
            {"Home": "README.md"},
            {"Getting started": ["QUICKSTART.md"]},
            {"Guides": ["design/DECISIONS.md"]},
            {"Reference": ["CLI_GUIDE.md"]},
            {"Contributors": []},
        ],
        [
            {"Home": "README.md"},
            {"Getting started": ["QUICKSTART.md"]},
            {"Guides": ["NAMING.md"]},
            {"Reference": ["CLI_GUIDE.md"]},
            {"Contributors": [{"Design": ["maintainers/README.md"]}]},
        ],
    ],
)
def test_the_grouping_check_rejects_records_in_the_users_path(nav: object) -> None:
    with pytest.raises(AssertionError):
        _check_grouping(nav)


@pytest.mark.parametrize(
    "page",
    [
        "QUICKSTART.md",
        "GLOSSARY.md",
        "COMPARISON.md",
        "GETTING_HELP.md",
        "KNOWN_LIMITATIONS.md",
        "CLI_GUIDE.md",
    ],
)
def test_the_users_pages_are_in_the_users_part_of_the_navigation(page: str) -> None:
    groups = {target: group for group, target in _entries(_project()["nav"])}
    assert page in groups
    assert not set(groups[page]) & {*RECORD_GROUPS, CONTRIBUTORS}


def test_headings_get_githubs_anchors_so_one_link_works_in_both_places() -> None:
    extensions = _project()["markdown_extensions"]
    assert isinstance(extensions, dict)
    slugify = extensions["toc"]["slugify"]
    assert slugify == {"object": "pymdownx.slugs.slugify", "kwds": {"case": "lower"}}


def test_the_site_is_published_where_the_package_metadata_says() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["urls"]["Documentation"] == _project()["site_url"]


def test_ci_builds_the_site_with_the_command_just_docs_runs() -> None:
    """CI's documentation job is the strict build a contributor runs locally."""
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    recipe = justfile.split("\ndocs:\n", 1)[1].split("\n\n", 1)[0].strip()
    assert recipe == "uv run --frozen --only-group docs zensical build --strict --clean"
    workflow = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    assert f"- run: {recipe}\n" in workflow
    assert "branches: [main]" in workflow and "pull_request:" in workflow
