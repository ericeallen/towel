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
        if link.scheme or link.netloc:
            continue
        path = unquote(link.path)
        target = posixpath.normpath(str(PurePosixPath(name).parent / path)) if path else name
        if path.startswith("/"):
            target = path.removeprefix("/")
        result.append((target, unquote(link.fragment)))
    return result


def _check_links(documents: Mapping[str, str], files: set[str]) -> None:
    inspected = 0
    for name, markdown in documents.items():
        for target, fragment in _local_links(name, markdown):
            inspected += 1
            assert target in files, f"{name}: missing local destination {target}"
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


def test_every_guide_is_indexed_and_links_back() -> None:
    _check_index(_documents(), "docs/README.md", "docs")


def test_every_proposal_is_indexed_and_links_back() -> None:
    _check_index(_documents(), "docs/proposals/README.md", "docs/proposals")


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
