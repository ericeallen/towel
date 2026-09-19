"""Keep the README usable as PyPI's repository-independent description."""

from pathlib import Path, PurePosixPath
import re
import tomllib
from urllib.parse import unquote, urlsplit

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


def test_readme_links_are_absolute_or_fragment_only() -> None:
    destinations = _destinations((ROOT / "README.md").read_text())
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
        invalid.append(destination)
    assert not invalid, f"README contains {len(invalid)} nonportable destinations: {invalid}"


def test_readme_repository_links_match_version_and_existing_paths() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    documentation_links = []
    for destination in _destinations((ROOT / "README.md").read_text()):
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
    assert documentation_links, "README must retain links to its versioned repository documentation"
