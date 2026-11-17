# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""A shorter README must not make the larger documentation set harder to find.

The owner requested a readable entry point and a navigable documentation book.
Check that each guide has an index entry and a way back, and that local links
resolve to real files and real prose headings. Do not count headings hidden in
code examples as destinations. These tests protect navigation, not a preferred
word count or an immutable layout; factual release regressions remain separate.

The pages under docs/ are also the documentation site, which serves nothing outside
docs/. A page there names a repository file outside docs/ by its GitHub address on
the default branch, and that address is checked against this checkout like a
relative link.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit

import pytest

from tests.test_readme_links import _destinations

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_FILES = re.compile(r"https://github\.com/ericeallen/towel/(?:blob|tree)/main/([^#?]+)")
# Retained release evidence is never edited, so its links keep naming where these
# records were when it was recorded. They moved in 2.0, when the user guides and the
# project's records were separated; at the evidence's own commit the old paths exist.
MOVED_SINCE_RECORDED = {
    "docs/PRODUCTION_READINESS.md": "docs/maintainers/PRODUCTION_READINESS.md",
    "docs/RELEASING.md": "docs/maintainers/RELEASING.md",
    "docs/proposals/next-validation-final.md": "docs/design/proposals/next-validation-final.md",
    "docs/GENERATED_CODE.md": "docs/design/GENERATED_CODE_RULES.md",
    "docs/PERFORMANCE.md": "docs/maintainers/PERFORMANCE_HISTORY.md",
}
FROZEN_EVIDENCE = "tests/release_evidence/"
# Each documentation section: its index, and the directory whose pages it lists.
SECTIONS = (
    ("docs/README.md", "docs"),
    ("docs/design/README.md", "docs/design"),
    ("docs/design/proposals/README.md", "docs/design/proposals"),
    ("docs/maintainers/README.md", "docs/maintainers"),
)


def _prose(markdown: str) -> str:
    return re.sub(r"(?ms)^```[^\n]*\n.*?^```[ \t]*$", "", markdown)


def _anchors(markdown: str) -> set[str]:
    prose = _prose(markdown)
    found = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)["\']', prose))
    used: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6} (.+?)\s*#*\s*$", prose, re.MULTILINE):
        heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        duplicate = used.get(slug, 0)
        used[slug] = duplicate + 1
        found.add(f"{slug}-{duplicate}" if duplicate else slug)
    return found


def _local_links(name: str, markdown: str) -> list[tuple[str, str]]:
    result = []
    for destination in _destinations(markdown):
        link = urlsplit(destination)
        repository_file = REPOSITORY_FILES.fullmatch(destination.split("#", 1)[0])
        if repository_file:
            target = posixpath.normpath(unquote(repository_file.group(1)))
            result.append((target, unquote(link.fragment)))
            continue
        if link.scheme or link.netloc:
            continue
        path = unquote(link.path)
        target = posixpath.normpath(str(PurePosixPath(name).parent / path)) if path else name
        if path.startswith("/"):
            target = path.removeprefix("/")
        if name.startswith(FROZEN_EVIDENCE):
            target = MOVED_SINCE_RECORDED.get(target, target)
        result.append((target, unquote(link.fragment)))
    return result


def _relative_links_leaving_docs(documents: Mapping[str, str]) -> list[str]:
    """Relative links from a site page to a file the site does not serve."""
    leaving = []
    for name, markdown in documents.items():
        if not name.startswith("docs/"):
            continue
        for destination in _destinations(markdown):
            link = urlsplit(destination)
            if link.scheme or link.netloc or not link.path:
                continue
            target = posixpath.normpath(str(PurePosixPath(name).parent / unquote(link.path)))
            if not target.startswith("docs/"):
                leaving.append(f"{name}: {destination}")
    return leaving


def _check_links(documents: Mapping[str, str], files: set[str]) -> None:
    inspected = 0
    for name, markdown in documents.items():
        for target, fragment in _local_links(name, markdown):
            inspected += 1
            assert target in files or (
                (ROOT / target).is_dir() and not fragment
            ), f"{name}: missing local destination {target}"
            if fragment and target.endswith(".md"):
                assert target in documents, f"{name}: uninspected heading destination {target}"
                assert fragment in _anchors(
                    documents[target]
                ), f"{name}: missing {target}#{fragment}"
    assert inspected, "The link check must not silently inspect nothing"


def _check_index(documents: Mapping[str, str], index: str, directory: str) -> None:
    expected = {
        name for name in documents if str(PurePosixPath(name).parent) == directory and name != index
    }
    assert expected, "The index check must inspect a nonempty documentation section"
    linked = {target for target, _ in _local_links(index, documents[index])}
    assert expected <= linked, f"Missing index entries: {sorted(expected - linked)}"
    for name in expected:
        destinations = {target for target, _ in _local_links(name, documents[name])}
        assert index in destinations, f"{name} has no link back to {index}"


def _documents() -> dict[str, str]:
    paths = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    # Links into retained historical evidence are inspected without rewriting it.
    paths.extend((ROOT / "tests/release_evidence").rglob("*.md"))
    return {str(path.relative_to(ROOT)): path.read_text() for path in paths}


def test_documentation_local_links_and_contents_resolve() -> None:
    documents = _documents()
    files = set(documents)
    for name, markdown in documents.items():
        for target, _ in _local_links(name, markdown):
            if (ROOT / target).is_file():
                files.add(target)
    _check_links(documents, files)


@pytest.mark.parametrize(("index", "directory"), SECTIONS)
def test_every_page_is_indexed_and_links_back(index: str, directory: str) -> None:
    _check_index(_documents(), index, directory)


def test_every_directory_of_pages_has_a_section_index() -> None:
    directories = {
        str(PurePosixPath(name).parent)
        for name in _documents()
        if name.startswith("docs/") and not name.startswith("docs/maintainers/reviews/")
    }
    assert directories == {directory for _, directory in SECTIONS}


def test_site_pages_link_outside_docs_only_by_github_address() -> None:
    """The site serves docs/ alone; Zensical 0.1 turns `../X.md` into a link to its parent."""
    assert _relative_links_leaving_docs(_documents()) == []


def test_links_leaving_docs_are_found() -> None:
    documents = {
        "docs/guide.md": "[Up](../CHANGELOG.md) [Same](other.md) [Web](https://example.org/)",
        "docs/sub/page.md": "[Index](../README.md) [Source](../../src/towel/cli.py)",
        "CONTRIBUTING.md": "[Guide](docs/guide.md) [Up](../outside.md)",
    }
    assert _relative_links_leaving_docs(documents) == [
        "docs/guide.md: ../CHANGELOG.md",
        "docs/sub/page.md: ../../src/towel/cli.py",
    ]


def test_github_addresses_on_main_are_checked_as_local_files() -> None:
    files = {"docs/guide.md", "CHANGELOG.md"}
    good = {"docs/guide.md": "[Log](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md)"}
    _check_links({**good, "CHANGELOG.md": "# Changelog\n"}, files)
    bad = {"docs/guide.md": "[Gone](https://github.com/ericeallen/towel/blob/main/GONE.md)"}
    with pytest.raises(AssertionError, match="missing local destination GONE.md"):
        _check_links(bad, files)
    anchor = {
        "docs/guide.md": "[Log](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md#x)"
    }
    with pytest.raises(AssertionError, match="missing CHANGELOG.md#x"):
        _check_links({**anchor, "CHANGELOG.md": "# Changelog\n"}, files)


def test_only_frozen_evidence_may_name_a_record_where_it_was() -> None:
    link = "[Readiness](../../../docs/PRODUCTION_READINESS.md)"
    evidence = "tests/release_evidence/x/README.md"
    assert _local_links(evidence, link) == [("docs/maintainers/PRODUCTION_READINESS.md", "")]
    assert _local_links("tests/x/y/README.md", link) == [("docs/PRODUCTION_READINESS.md", "")]


@pytest.mark.parametrize("destination", ["missing.md", "guide.md#missing", "guide.md#fake"])
def test_navigation_check_rejects_broken_paths_and_nonexistent_headings(destination: str) -> None:
    documents = {
        "index.md": f"# Index\n\n[Guide]({destination})\n",
        "guide.md": "# Guide\n\n```python\n## Fake\n```\n",
    }
    with pytest.raises(AssertionError):
        _check_links(documents, set(documents))


def test_index_check_rejects_orphaned_guides() -> None:
    documents = {
        "docs/README.md": "# Index\n\n[One](one.md)\n",
        "docs/one.md": "# One\n\n[Index](README.md)\n",
        "docs/two.md": "# Two\n\n[Index](README.md)\n",
    }
    with pytest.raises(AssertionError, match="Missing index entries"):
        _check_index(documents, "docs/README.md", "docs")


def test_heading_check_ignores_fenced_examples_and_numbers_duplicate_anchors() -> None:
    markdown = "# Guide\n\n## Topic\n\n```python\n## Topic\n```\n\n## Topic\n"
    assert _anchors(markdown) == {"guide", "topic", "topic-1"}
