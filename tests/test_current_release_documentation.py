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
For the same reason no shipped front-door file may say whether its own version
has been published: publication happens after the freeze.
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

from scripts import verify_release_visibility as gate
from tests.test_next_validation_documentation import _number, _rows, _table
from tests.test_readme_links import _check_repository_links

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("README.md", "SECURITY.md", "CHANGELOG.md", "CONTRIBUTING.md", "docs/RELEASING.md")


def _check_current_version(documents: Mapping[str, str], version: str) -> None:
    assert f"**Release status: {version} (beta).**" in documents["README.md"]
    assert f"This source tree targets **{version}**" in documents["SECURITY.md"]
    assert f"## [{version}]\n" in documents["CHANGELOG.md"]
    _check_repository_links(documents["README.md"], version)
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


@pytest.mark.parametrize(
    "name", ["README.md", "SECURITY.md", "CONTRIBUTING.md", "docs/README.md", "docs/QUICKSTART.md"]
)
def test_shipped_front_door_never_states_its_own_publication_status(name: str) -> None:
    """User-perspective audit of 1.949, finding 4: PyPI says the release is unpublished.

    The README frozen into 1.949 reads "this candidate is unpublished" and "Latest
    published release: 1.948" on PyPI, and SECURITY.md, CONTRIBUTING.md, the
    documentation index and the quick start shipped the same kind of sentence. These
    files are frozen before publication, so any statement of whether their version is
    published is false afterwards and cannot be corrected. They name the version they
    describe; the GitHub release notes record what was published.
    """
    document = " ".join((ROOT / name).read_text().split()).casefold()
    for phrase in (
        "unpublished",
        "preparation checkpoint",
        "latest published release",
        "this source documents",
    ):
        assert phrase not in document, f"{name} states its own publication status: {phrase!r}"


def test_the_release_gate_refuses_the_same_files_and_phrases() -> None:
    """One rule, checked here on the checkout and by `just release` on GitHub."""
    assert set(gate.STATUS_DOCUMENTS) == {
        "README.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/README.md",
        "docs/QUICKSTART.md",
    }
    assert set(gate.PUBLICATION_CLAIMS) == {
        "unpublished",
        "preparation checkpoint",
        "latest published release",
        "this source documents",
    }
    for name in gate.STATUS_DOCUMENTS:
        assert gate.publication_claims((ROOT / name).read_text()) == ()


@pytest.mark.parametrize(
    "sentence,phrase",
    [
        ("this candidate is Unpublished and its validation", "unpublished"),
        ("At the November 8, 2026 preparation\ncheckpoint, it", "preparation checkpoint"),
        ("At that checkpoint: **Latest published release: 1.948.**", "latest published release"),
        ("The latest published\n  release at that point", "latest published release"),
        ("- This source documents 1.948.", "this source documents"),
    ],
)
def test_the_status_check_sees_each_phrase_whatever_its_case_or_wrapping(
    sentence: str, phrase: str
) -> None:
    """The 1.949 sentences were wrapped mid-phrase; a line-by-line search misses them."""
    document = (ROOT / "SECURITY.md").read_text() + "\n" + sentence + "\n"
    assert gate.publication_claims(document) == (phrase,)


def test_the_source_to_be_frozen_satisfies_the_live_gate_document_rules() -> None:
    """`just release` reads these documents from GitHub only after CI and promotion.

    Apply its rules to the checkout first, where a refusal costs an edit and not a
    repeated release step: one status line, the security target, the changelog
    heading, tag-pinned links to files that exist, and no statement of publication.
    """
    readme = (ROOT / "README.md").read_text()
    if "**Development branch:" in readme:
        pytest.skip("the gate refuses a development README by design; it is finalized first")
    version = str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    documents = {
        name: (ROOT / name).read_text()
        for name in ("pyproject.toml", "CHANGELOG.md", *gate.STATUS_DOCUMENTS)
    }
    for name in gate.document_links(readme, "ericeallen/towel", version):
        documents.setdefault(name, (ROOT / name).read_text())
    gate.verify_documents(documents, version, "ericeallen/towel")


def test_package_metadata_describes_the_tool_in_the_words_people_search_for() -> None:
    """User-perspective audit of 1.949, finding 4 (discovery report D4 and D10).

    PyPI showed a summary without "duplicate" or "refactor", no keywords, a library
    classifier on a command-line tool and no typing marker although the wheel ships
    `py.typed`. The platform classifiers follow the README's Requirements sentence.
    """
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = configuration["project"]
    summary = str(project["description"]).casefold()
    assert "duplicate" in summary and "refactor" in summary
    assert "\n" not in summary, "The core metadata Summary field is one line"
    assert {"refactoring", "duplicate code", "code duplication"} <= set(project["keywords"])
    classifiers = set(project["classifiers"])
    assert {
        "Environment :: Console",
        "Operating System :: MacOS",
        "Operating System :: POSIX :: Linux",
        "Typing :: Typed",
    } <= classifiers
    assert "Topic :: Software Development :: Libraries :: Python Modules" not in classifiers
    assert "(macOS or Linux)" in (ROOT / "README.md").read_text()
    assert configuration["tool"]["setuptools"]["package-data"]["towel"] == ["py.typed"]
    assert (ROOT / "src/towel/py.typed").is_file(), "Typing :: Typed is a claim about the wheel"


def _check_project_urls(urls: Mapping[str, str], version: str) -> None:
    assert {"Homepage", "Documentation", "Changelog", "Issues", "Repository"} <= set(urls)
    for label, url in urls.items():
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc, f"{label} is not an absolute link"
        assert version not in url and not re.search(
            r"/v?\d+\.\d+", parsed.path
        ), f"{label} names a release and would go stale on the next one: {url}"


def test_project_urls_cannot_go_stale_with_a_release() -> None:
    """`just bump-version` rewrites one line of pyproject.toml, so no link may need rewriting.

    The README's links are pinned to the release tag, and the release gate checks them.
    Nothing checks these, so they name no tag: the Documentation and Changelog links
    follow the default branch.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    _check_project_urls(project["urls"], str(project["version"]))
    for stale in (
        "https://github.com/ericeallen/towel/blob/v1.949/docs/README.md",
        "https://github.com/ericeallen/towel/releases/tag/v1.949",
        "https://pypi.org/project/code-towel/1.949/",
        "docs/README.md",
    ):
        with pytest.raises(AssertionError):
            _check_project_urls({**project["urls"], "Documentation": stale}, "1.949")


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


def test_historical_1792_changes_keep_the_october_policy_and_measurement() -> None:
    """A new version must not rewrite the preceding release's policy or evidence."""
    changes = (ROOT / "CHANGELOG.md").read_text().split("## [1.792]\n", 1)[1]
    changes = changes.split("\n## ", 1)[0]
    assert "self-instrumentation" in changes
    assert "october-2-complete-cli-comparison" in changes
    assert "Recognized body instrumentation is protected" not in changes
    assert "explicit-decorator and import-time checks remain" not in changes


def test_historical_release_summaries_retain_the_completed_october_measurements() -> None:
    """Recompute the historical means without promoting them to current timings."""
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


def test_preparation_performance_summaries_match_all_retained_samples() -> None:
    evidence = _table(
        json.loads(
            (ROOT / "tests/release_evidence/1.946-preparation/measurements.json").read_text()
        )
    )
    runs = _rows(evidence["runs"])
    assert len(runs) == 8
    assert evidence["planned_version_not_measured"] == "1.946"
    assert evidence["package_metadata_version_both_roles"] == "1.792"
    assert evidence["all_owned_workers_closed"] is True
    assert evidence["active_container_count"] == 0
    documents = {
        name: " ".join((ROOT / name).read_text().split())
        for name in (
            "docs/RELEASE_HISTORY.md",
            "CHANGELOG.md",
            "docs/RELEASE_1_946.md",
            "docs/PERFORMANCE.md",
        )
    }
    for project in ("click", "rich"):
        selected = [row for row in runs if row["project"] == project]
        assert [row["role"] for row in selected] == ["RELEASE", "FIXED", "FIXED", "RELEASE"]
        seconds = {
            role: mean(_number(row["CLI_seconds"]) for row in selected if row["role"] == role)
            for role in ("RELEASE", "FIXED")
        }
        percentage = 100 * (seconds["FIXED"] / seconds["RELEASE"] - 1)
        for text in documents.values():
            for value in seconds.values():
                assert any(f"{value:.{precision}f}" in text for precision in (2, 3))
            assert f"{percentage:.2f}%" in text
        for row in selected:
            assert row["CLI_exit_code"] == 0
            assert row["original_pair_equal"] is True
            assert row["visible_payloads_equal"] is True
            assert row["all_source_artifact_runtime_driver_proofs_exact"] is True
            assert row["owned_workers_closed"] is True


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
    code = (ROOT / "docs/QUICKSTART.md").read_text().split("```python\n", 1)[1].split("```", 1)[0]
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
    observe = (
        "\nobservations = [(checkout_total(n), quote_total(n), invoice_total(n)) "
        "for n in range(-5, 151)]"
    )
    exec(code + observe, before)
    exec(output + observe, after)
    assert before["observations"] == after["observations"]
