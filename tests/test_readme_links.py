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

"""Keep development navigation current and release README links portable on PyPI."""

from pathlib import Path, PurePosixPath
import re
import tomllib
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _destinations(markdown: str) -> list[str]:
    # Inspect inline/image links (including linked badges) and reference
    # definitions, excluding the fenced examples used in this README.
    prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```[ \t]*$", "", markdown)
    matches = re.findall(
        r"\]\(\s*(?:<([^>]*)>|([^\s)]+))" r"|^ {0,3}\[[^\]]+\]:\s*(?:<([^>]*)>|(\S+))",
        prose,
        re.MULTILINE,
    )
    return [next((part for part in match if part), "") for match in matches]


def _check_readme_destinations(markdown: str, *, development: bool = False) -> None:
    destinations = _destinations(markdown)
    assert destinations, "README link inspection must not silently inspect nothing"
    invalid = []
    for destination in destinations:
        url = urlsplit(destination)
        if destination.startswith("#"):
            continue
        if url.scheme == "https" and url.netloc:
            continue
        if url.scheme == "mailto" and url.path:
            continue
        if development and not url.scheme and not url.netloc:
            relative = PurePosixPath(unquote(url.path))
            if not relative.is_absolute() and ".." not in relative.parts:
                assert (ROOT / relative).is_file(), destination
                continue
        invalid.append(destination)
    assert not invalid, f"README contains {len(invalid)} nonportable destinations: {invalid}"


def test_readme_links_are_portable_or_explicit_development_links() -> None:
    markdown = (ROOT / "README.md").read_text()
    _check_readme_destinations(markdown, development="**Development branch:**" in markdown)


def test_published_readme_still_rejects_repository_relative_links() -> None:
    with pytest.raises(AssertionError, match="nonportable"):
        _check_readme_destinations("[Quick start](docs/QUICKSTART.md)")


def test_development_readme_links_to_current_guides() -> None:
    markdown = (ROOT / "README.md").read_text()
    if "**Development branch:**" in markdown:
        destinations = _destinations(markdown)
        for guide in ("QUICKSTART", "NAMING", "GENERATED_CODE", "CLI_GUIDE"):
            assert f"docs/{guide}.md" in destinations


def _check_repository_links(markdown: str, version: str) -> None:
    """Current guides follow the source; published evidence keeps its stated identity."""
    current, separator, historical = markdown.partition("## Releases and validation\n")
    assert separator, "README must distinguish current guides from published release evidence"
    published = re.search(r"\*\*Latest published release: ([0-9][^ *]+)\.\*\*", current)
    assert published is not None, "README must identify the latest published version separately"
    documentation_links = []
    for section, expected in ((current, version), (historical, published.group(1))):
        for destination in _destinations(section):
            url = urlsplit(destination)
            prefix = "/ericeallen/towel/blob/"
            if url.hostname != "github.com" or not url.path.startswith(prefix):
                continue
            tag, separator, path = unquote(url.path.removeprefix(prefix)).partition("/")
            assert tag == f"v{expected}", f"README link uses a different release: {destination}"
            relative = PurePosixPath(path)
            assert separator and path and not relative.is_absolute() and ".." not in relative.parts
            assert (ROOT / path).is_file(), f"README link names a missing file: {destination}"
            documentation_links.append(destination)
    assert documentation_links, "README must retain its versioned publication evidence"


def test_readme_repository_links_match_version_and_existing_paths() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    _check_repository_links((ROOT / "README.md").read_text(), version)


def test_current_guide_cannot_point_at_the_previous_release() -> None:
    markdown = (
        "**Latest published release: 1.948.**\n"
        "[Quick start](https://github.com/ericeallen/towel/blob/v1.948/docs/QUICKSTART.md)\n"
        "## Releases and validation\n"
        "[Evidence](https://github.com/ericeallen/towel/blob/v1.948/docs/PRODUCTION_READINESS.md)"
    )
    with pytest.raises(AssertionError, match="different release"):
        _check_repository_links(markdown, "1.949")


def test_published_evidence_cannot_be_relabelled_as_the_candidate() -> None:
    markdown = (
        "**Latest published release: 1.948.**\n"
        "[Quick start](docs/QUICKSTART.md)\n"
        "## Releases and validation\n"
        "[Evidence](https://github.com/ericeallen/towel/blob/v1.949/docs/PRODUCTION_READINESS.md)"
    )
    with pytest.raises(AssertionError, match="different release"):
        _check_repository_links(markdown, "1.949")


def test_r9p2_the_test_readme_lists_the_example_corpus_it_describes() -> None:
    """tests/README once stated counts that went stale; its corpus list is checked instead."""
    readme = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("Each file of the corpus") : readme.index("## Test Coverage")]
    listed = re.findall(r"^- `([^`]+\.py)`", section, re.MULTILINE)
    assert listed == sorted(path.name for path in (ROOT / "test_examples").glob("*.py"))
