# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A correct release tag is insufficient when GitHub's front page remains old."""

import base64
from collections.abc import Callable
import json
from pathlib import Path

import pytest

from scripts import verify_release_visibility as gate

RELEASE = "a" * 40
HEAD = "b" * 40
PREFIX = "repos/ericeallen/towel"
VERSION = "1.947"


def remote(
    *, branch: str = "trunk", status: str = "ahead", base: str = RELEASE
) -> dict[str, bytes]:
    """Default branch advances for documentation while the published tag stays fixed."""
    objects: dict[str, object] = {
        PREFIX: {"default_branch": branch},
        f"{PREFIX}/commits/{branch}": {"sha": HEAD},
        f"{PREFIX}/compare/{RELEASE}...{HEAD}": {
            "status": status,
            "merge_base_commit": {"sha": base},
        },
    }
    documents = {
        "pyproject.toml": '[project]\nversion = "1.947"\n',
        "README.md": "**Release status: 1.947 (beta).** Published.\n"
        "[Guide](https://github.com/ericeallen/towel/blob/v1.947/docs/GUIDE.md)\n",
        "SECURITY.md": "This source tree targets **1.947**.\n",
        "CHANGELOG.md": "## [Unreleased]\n\n## [1.947]\nPublished.\n",
        "docs/GUIDE.md": "# A current guide\n",
    }
    for path, content in documents.items():
        objects[f"{PREFIX}/contents/{path}?ref={HEAD}"] = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        }
    return {name: json.dumps(value).encode() for name, value in objects.items()}


def request_for(responses: dict[str, bytes]) -> Callable[[str], bytes]:
    return responses.__getitem__


def replace_document(responses: dict[str, bytes], path: str, before: str, after: str) -> None:
    endpoint = f"{PREFIX}/contents/{path}?ref={HEAD}"
    record = gate.object_value(json.loads(responses[endpoint]))
    content = base64.b64decode(gate.text(record["content"])).decode()
    assert before in content
    record["content"] = base64.b64encode(content.replace(before, after).encode()).decode()
    responses[endpoint] = json.dumps(record).encode()


def test_default_branch_name_is_discovered_and_postrelease_documentation_is_allowed() -> None:
    result = gate.verify(VERSION, RELEASE, request=request_for(remote()))
    assert result == gate.Visibility("trunk", HEAD, RELEASE, 5)


def test_default_branch_at_exact_release_is_allowed() -> None:
    responses = {
        key.replace(HEAD, RELEASE): value for key, value in remote(status="identical").items()
    }
    responses[f"{PREFIX}/commits/trunk"] = json.dumps({"sha": RELEASE}).encode()
    result = gate.verify(VERSION, RELEASE, request=request_for(responses))
    assert result.head == RELEASE


@pytest.mark.parametrize("status,base", [("behind", HEAD), ("diverged", HEAD), ("ahead", HEAD)])
def test_release_tag_and_correct_version_cannot_hide_missing_default_branch_history(
    status: str, base: str
) -> None:
    responses = remote(status=status, base=base)
    with pytest.raises(gate.VisibilityError, match="does not contain validated release"):
        gate.verify(VERSION, RELEASE, request=request_for(responses))


@pytest.mark.parametrize("path", ["pyproject.toml", "README.md", "SECURITY.md", "CHANGELOG.md"])
def test_containing_the_release_does_not_excuse_stale_public_documentation(path: str) -> None:
    responses = remote()
    replace_document(responses, path, VERSION, "1.792")
    with pytest.raises(gate.VisibilityError):
        gate.verify(VERSION, RELEASE, request=request_for(responses))


def test_correct_status_with_old_documentation_links_is_rejected() -> None:
    responses = remote()
    replace_document(responses, "README.md", "blob/v1.947/", "blob/v1.792/")
    with pytest.raises(gate.VisibilityError, match="stale link"):
        gate.verify(VERSION, RELEASE, request=request_for(responses))


@pytest.mark.parametrize("kind", ["blob", "tree"])
def test_current_guide_cannot_hide_an_additional_old_release_link(kind: str) -> None:
    responses = remote()
    replace_document(
        responses,
        "README.md",
        "Published.",
        f"Published. [Old docs](https://github.com/ericeallen/towel/{kind}/v1.792/docs)",
    )
    with pytest.raises(gate.VisibilityError, match="stale link"):
        gate.verify(VERSION, RELEASE, request=request_for(responses))


def test_ambiguous_status_and_missing_links_are_rejected() -> None:
    for replacement in ("**Release status: 1.792 (beta).**\nPublished.", "Published."):
        responses = remote()
        before = (
            "Published." if "Release status" in replacement else "**Release status: 1.947 (beta).**"
        )
        replace_document(responses, "README.md", before, replacement)
        with pytest.raises(gate.VisibilityError, match="release status"):
            gate.verify(VERSION, RELEASE, request=request_for(responses))
    responses = remote()
    replace_document(responses, "README.md", "https://github.com/", "https://example.com/")
    with pytest.raises(gate.VisibilityError, match="no version-pinned"):
        gate.verify(VERSION, RELEASE, request=request_for(responses))


def test_missing_or_truncated_remote_files_fail_closed() -> None:
    for record in ({"type": "file", "encoding": "none", "content": ""}, {}):
        responses = remote()
        responses[f"{PREFIX}/contents/docs/GUIDE.md?ref={HEAD}"] = json.dumps(record).encode()
        with pytest.raises(gate.VisibilityError, match="complete file contents"):
            gate.verify(VERSION, RELEASE, request=request_for(responses))


def test_network_failure_is_a_failed_gate_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object) -> gate.Visibility:
        raise OSError("GitHub unavailable")

    monkeypatch.setattr(gate, "verify", unavailable)
    assert gate.main([VERSION, "--commit", RELEASE]) == 1


def test_evidence_version_must_match_request(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"version": "1.792", "validation_commit": RELEASE}))
    assert gate.main([VERSION, "--evidence", str(path)]) == 1


def test_supported_release_recipe_cannot_omit_the_live_default_branch_gate() -> None:
    source = (Path(__file__).resolve().parents[1] / "justfile").read_text()
    recipe = source.split("\nrelease VERSION EVIDENCE:\n", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("verify_release_evidence.py") < recipe.index("verify_release_visibility.py")
    assert "--evidence {{quote(EVIDENCE)}}" in recipe
    local = source.split("\nverify-release-evidence VERSION EVIDENCE:\n", 1)[1].split("\n\n", 1)[0]
    assert "verify_release_visibility" not in local
