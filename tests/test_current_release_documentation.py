# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Keep release-facing metadata coherent without rewriting historical evidence.

PyPI freezes the README in each distribution. A later documentation edit cannot
repair stale version links there. Check current metadata against the configured
version; historical benchmark versions and results must retain their identities.
The directory example must exercise fresh-source sequencing, since applying a
second proposal from an obsolete analysis correctly raises StaleSource.
"""

from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
from statistics import mean
import tomllib
from urllib.parse import unquote, urlsplit

import pytest

from tests.test_next_validation_documentation import _number, _rows, _table

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("README.md", "SECURITY.md", "CHANGELOG.md", "CONTRIBUTING.md", "docs/RELEASING.md")


def _check_current_version(documents: Mapping[str, str], version: str) -> None:
    assert f"**Release status: {version} (beta).**" in documents["README.md"]
    assert f"This source tree targets **{version}**" in documents["SECURITY.md"]
    assert f"## [{version}]\n" in documents["CHANGELOG.md"]
    links = re.findall(r"https://github.com/ericeallen/towel/blob/([^/]+)/", documents["README.md"])
    assert links and set(links) == {f"v{version}"}
    for name in ("CONTRIBUTING.md", "docs/RELEASING.md"):
        assert "`just release VERSION /path/to/release-evidence.json`" in documents[name]
        assert "`just release VERSION`" not in documents[name]


def test_current_release_metadata_and_readme_tags_match_the_package() -> None:
    version = str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    _check_current_version({name: (ROOT / name).read_text() for name in PAGES}, version)


@pytest.mark.parametrize("name", PAGES)
def test_stale_current_version_and_release_commands_are_rejected(name: str) -> None:
    documents = {path: (ROOT / path).read_text() for path in PAGES}
    version = str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    if name in {"CONTRIBUTING.md", "docs/RELEASING.md"}:
        documents[name] = documents[name].replace(
            "just release VERSION /path/to/release-evidence.json", "just release VERSION"
        )
    else:
        documents[name] = documents[name].replace(version, "0.0")
    with pytest.raises(AssertionError):
        _check_current_version(documents, version)


def _check_preservation_contract(text: str) -> None:
    compact = " ".join(text.split())
    assert "programs that do not use reflection or self-instrumentation" in compact
    assert "including through code they call" in compact
    assert "arbitrary callbacks" not in compact
    assert "external side effects limit what can be established" not in compact


@pytest.mark.parametrize("name", ["README.md", "SECURITY.md", "docs/GENERATED_CODE.md"])
def test_current_guides_state_the_positive_preservation_contract(name: str) -> None:
    _check_preservation_contract((ROOT / name).read_text())


def test_a_blanket_callback_exclusion_is_not_the_preservation_contract() -> None:
    original = (ROOT / "docs/GENERATED_CODE.md").read_text()
    with pytest.raises(AssertionError):
        _check_preservation_contract(original + "\nUnsupported: arbitrary callbacks.\n")


def test_october_changes_belong_to_the_version_being_prepared() -> None:
    """The pending release cannot promise both retired protections and their removal."""
    version = str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    changes = (ROOT / "CHANGELOG.md").read_text().split(f"## [{version}]\n", 1)[1]
    changes = changes.split("\n## ", 1)[0]
    assert "self-instrumentation" in changes
    assert "october-2-complete-cli-comparison" in changes
    assert "Recognized body instrumentation is protected" not in changes
    assert "explicit-decorator and import-time checks remain" not in changes


def test_current_release_summaries_use_the_completed_october_measurements() -> None:
    """Recompute the quoted means from samples; earlier comparisons remain historical."""
    evidence = _table(
        json.loads(
            (ROOT / "tests/release_evidence/post-1772-typed-cli/measurements.json").read_text()
        )
    )
    runs = _rows(_table(evidence["measurements"])["ordinary"])
    seconds: dict[str, float] = {}
    for arm in ("candidate", "published"):
        samples = [_number(row["wall_seconds"]) for row in runs if row["arm"] == arm]
        assert len(samples) == 2
        seconds[arm] = mean(samples)
    phrase = f"{seconds['candidate']:.2f} seconds versus {seconds['published']:.2f} seconds"
    for name in ("CHANGELOG.md", "docs/PRODUCTION_READINESS.md", "docs/RELEASE_LOG.md"):
        assert phrase in " ".join((ROOT / name).read_text().split())


def test_readme_release_document_links_resolve_in_the_source_to_be_tagged() -> None:
    """Check local destinations now; remote tag URLs are checked after tagging."""
    links = re.findall(
        r"https://github.com/ericeallen/towel/blob/[^)\s]+", (ROOT / "README.md").read_text()
    )
    assert links
    for link in links:
        parsed = urlsplit(link)
        target = ROOT / unquote(parsed.path.split("/", 5)[5])
        assert target.is_file(), link
        if parsed.fragment:
            headings = re.findall(r"^#+ (.+)$", target.read_text(), re.MULTILINE)
            anchors = {
                re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in headings
            }
            assert unquote(parsed.fragment) in anchors, link


def test_release_branch_can_receive_exact_commit_ci_without_changing_main() -> None:
    """A documented release branch must trigger push CI on its own commit."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    match = re.search(r"(?m)^  push:\n    branches: (\[.*\])$", workflow)
    assert match is not None
    # This workflow declares a literal YAML flow list. Bare names such as main
    # are YAML strings, not Python literals; reject shapes this parser cannot see.
    branches = [item.strip().strip("\"'") for item in match.group(1)[1:-1].split(",")]
    assert branches and all(re.fullmatch(r"[-\w/*]+", item) for item in branches)
    version = str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    assert any(fnmatchcase("release/" + version, pattern) for pattern in branches)
    assert any(fnmatchcase("main", pattern) for pattern in branches)
    procedure = " ".join((ROOT / "docs/RELEASING.md").read_text().split())
    assert "CI runs for pull requests and pushes to `main` or `release/**`" in procedure
    assert "run's `headSha` is the frozen release commit" in procedure


def test_directory_guide_example_reanalyzes_and_preserves_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    code = """def first(value):
    a = value + 1
    b = a * 2
    c = b - 3
    return c

def second(value):
    a = value + 1
    b = a * 2
    c = b - 3
    return c
"""
    (source / "example.py").write_text(code)
    guide = (ROOT / "docs/USAGE_GUIDE.md").read_text()
    section = guide.split("### 3. Analyze an Entire Directory\n", 1)[1]
    example = section.split("```python\n", 1)[1].split("```", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(example, "docs/USAGE_GUIDE.md", "exec"), namespace)
    assert namespace["reason"] == "fixed_point"
    assert (source / "example.py").read_text() == code
    output = (tmp_path / "src_cleaned/example.py").read_text()
    assert "__extracted_func_" in output
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(code + "\nobservations = [(first(n), second(n)) for n in range(-5, 6)]", before)
    exec(output + "\nobservations = [(first(n), second(n)) for n in range(-5, 6)]", after)
    assert before["observations"] == after["observations"]
