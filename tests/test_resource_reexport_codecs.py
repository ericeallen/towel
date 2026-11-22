# Copyright 2026 Eric Allen
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

"""A user-registered source codec is observable every time a provider is read."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from towel.analysis_sources import sharing_analysis_sources
from towel.source_text import read_source
from towel.unification.import_graph import ImportGraphCache
from towel.unification.resource_guard import ResourceImports, sharing_resource_answers


@pytest.mark.parametrize("consult_provider", [False, True])
@pytest.mark.parametrize("cookie", [b"# coding: ", b"# coding:\vutf-8 coding: "])
def test_reexport_decodes_only_a_consulted_provider_for_each_importer(
    tmp_path: Path, consult_provider: bool, cookie: bytes
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").touch()
    first, second = pkg / "first.py", pkg / "second.py"
    first.touch()
    second.touch()
    provider = pkg / "provider.py"
    provider.touch()
    graph = ImportGraphCache()
    graph.program_for(first)
    provider.write_bytes(cookie + b"towelresourcetest\n")
    decoded = []
    current = "def AttributeError(): pass\n"

    def decode(data: object, errors: str = "strict") -> tuple[str, int]:
        assert isinstance(data, (bytes, memoryview))
        decoded.append(current)
        return current, len(data)

    def lookup(name: str) -> codecs.CodecInfo | None:
        if name == "towelresourcetest":
            return codecs.CodecInfo(name=name, encode=codecs.utf_8_encode, decode=decode)
        return None

    origins = frozenset(
        {".provider.AttributeError" if consult_provider else ".unused.AttributeError"}
    )
    codecs.register(lookup)
    try:
        # Some Python versions also decode the cookie line when detecting
        # the encoding. Measure one ordinary provider read on this interpreter.
        assert read_source(provider) == current
        decodes_per_read = len(decoded)
        assert decodes_per_read
        decoded.clear()
        with sharing_analysis_sources(), sharing_resource_answers(graph, tmp_path):
            assert ResourceImports(str(first), graph).follow(origins) == set()
            first_source = current
            current = "from weakref import ref as AttributeError\n"
            expected = {"weakref.ref"} if consult_provider else set()
            assert ResourceImports(str(second), graph).follow(origins) == expected
        assert decoded == (
            [first_source] * decodes_per_read + [current] * decodes_per_read
            if consult_provider
            else []
        )
    finally:
        codecs.unregister(lookup)
