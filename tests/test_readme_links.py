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
    """Every repository document the README links is the one tagged with its own version.

    Until 1.949 a `## Releases and validation` section kept links at the tag named by a
    `**Latest published release: X.**` line. That line described the README's own
    publication and was false on PyPI (audit finding 4), and the live gate has always
    refused a link at any other tag, so the README now has one rule for all of them.
    """
    documentation_links = []
    for destination in _destinations(markdown):
        url = urlsplit(destination)
        prefix = "/ericeallen/towel/blob/"
        if url.hostname != "github.com" or not url.path.startswith(prefix):
            continue
        tag, separator, path = unquote(url.path.removeprefix(prefix)).partition("/")
        assert tag == f"v{version}", f"README link uses a different release: {destination}"
        relative = PurePosixPath(path)
        assert separator and path and not relative.is_absolute() and ".." not in relative.parts
        assert (ROOT / path).is_file(), f"README link names a missing file: {destination}"
        documentation_links.append(destination)
    assert documentation_links, "README must keep its version-pinned guide links"


def test_readme_repository_links_match_version_and_existing_paths() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    _check_repository_links((ROOT / "README.md").read_text(), version)


def test_current_guide_cannot_point_at_the_previous_release() -> None:
    markdown = "[Quick start](https://github.com/ericeallen/towel/blob/v1.948/docs/QUICKSTART.md)"
    with pytest.raises(AssertionError, match="different release"):
        _check_repository_links(markdown, "1.949")


def test_a_releases_section_cannot_keep_links_to_an_earlier_release() -> None:
    """Audit finding 4: no part of the README is exempt from naming its own version's tag."""
    markdown = (
        "[Quick start](https://github.com/ericeallen/towel/blob/v1.949/docs/QUICKSTART.md)\n"
        "## Releases\n"
        "[Evidence](https://github.com/ericeallen/towel/blob/v1.948/docs/PRODUCTION_READINESS.md)"
    )
    with pytest.raises(AssertionError, match="different release"):
        _check_repository_links(markdown, "1.949")


def test_a_readme_without_pinned_guides_is_not_accepted_as_having_checked_them() -> None:
    with pytest.raises(AssertionError, match="version-pinned guide links"):
        _check_repository_links("[Releases](https://github.com/ericeallen/towel/releases)", "1.949")


RELEASES = "https://github.com/ericeallen/towel/releases"
CONVENTION = (
    f"**Release status: 1.949 (beta).** See the [releases page]({RELEASES}).\n\n"
    "## Install\n\n"
    "[Quick start](https://github.com/ericeallen/towel/blob/v1.949/docs/QUICKSTART.md)\n\n"
    "## Releases\n\n"
    "The [changelog](https://github.com/ericeallen/towel/blob/v1.949/CHANGELOG.md)"
    f" and the [published versions]({RELEASES}).\n"
)


def _check_release_pointers(markdown: str, version: str) -> None:
    """The README says where publication is recorded, since it cannot say what was published."""
    status = [part for part in re.split(r"\n[ \t]*\n", markdown) if "**Release status:" in part]
    assert len(status) == 1, "README must have exactly one release-status paragraph"
    assert RELEASES in _destinations(status[0]), "The status paragraph must link the releases page"
    _, separator, rest = markdown.partition("\n## Releases\n")
    assert separator, "README must have a Releases section"
    destinations = _destinations(rest.split("\n## ", 1)[0])
    assert RELEASES in destinations, "The Releases section must link the releases page"
    changelog = f"https://github.com/ericeallen/towel/blob/v{version}/CHANGELOG.md"
    assert changelog in destinations, "The Releases section must link this version's changelog"


def test_readme_points_to_the_release_record_instead_of_restating_it() -> None:
    """Audit finding 4: PyPI's page for 1.949 says that 1.949 is unpublished.

    The README is frozen into the distributions before publication. It therefore
    names its version and links the page where publication is recorded afterwards.
    """
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    _check_release_pointers((ROOT / "README.md").read_text(), version)
    _check_release_pointers(CONVENTION, "1.949")


@pytest.mark.parametrize(
    "before,after",
    [
        (f"See the [releases page]({RELEASES}).", "See the releases page."),
        ("**Release status: 1.949 (beta).** See", "**Release status: 1.949 (beta).**\n\nSee"),
        ("## Releases\n", "## History\n"),
        (f" and the [published versions]({RELEASES})", ""),
        ("blob/v1.949/CHANGELOG.md", "blob/v1.949/docs/README.md"),
        ("blob/v1.949/CHANGELOG.md", "blob/v1.948/CHANGELOG.md"),
    ],
)
def test_release_pointer_check_detects_a_readme_that_lost_them(before: str, after: str) -> None:
    assert before in CONVENTION
    with pytest.raises(AssertionError):
        _check_release_pointers(CONVENTION.replace(before, after), "1.949")


def test_r9p2_the_test_readme_lists_the_example_corpus_it_describes() -> None:
    """tests/README once stated counts that went stale; its corpus list is checked instead."""
    readme = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("Each file of the corpus") : readme.index("## Test Coverage")]
    listed = re.findall(r"^- `([^`]+\.py)`", section, re.MULTILINE)
    assert listed == sorted(path.name for path in (ROOT / "test_examples").glob("*.py"))
