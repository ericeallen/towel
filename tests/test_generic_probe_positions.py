"""Adjacent extraction sites must not share checker evidence across scopes."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from towel.type_inference import (
    CheckSuccess,
    MypyInferrer,
    PyrightOracle,
    RevealKey,
    RevealRequest,
    TypeOracle,
)
from towel.unification.annotations import ApplySite
from towel.unification.generic_annotations import _signature_rows
from towel.unification.type_bindings import render_type


@pytest.fixture(params=["mypy", "pyright"])
def oracle(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    pytest.importorskip(request.param)
    checker: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    try:
        yield checker
    finally:
        checker.close()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("missing_result", [False, True])
def test_adjacent_result_and_argument_probes_keep_their_own_evidence(
    tmp_path: Path,
    oracle: TypeOracle,
    monkeypatch: pytest.MonkeyPatch,
    nested: bool,
    missing_result: bool,
) -> None:
    path = tmp_path / "program.py"
    indent = "        " if nested else "    "
    source = (
        "def sample(flag: bool) -> tuple[int, str]:\n"
        "    values = [1]\n"
        '    words = ["a"]\n'
        "    result = 0\n"
        + ("    if flag:\n" if nested else "")
        + f"{indent}result = values[0]\n"
        + f"{indent}result += 1\n"
        + "    result2 = words[0]\n"
        + '    result2 += "x"\n'
        + "    return result, result2\n"
    )
    path.write_text(source)
    assert oracle.check(str(path), source) == CheckSuccess()
    lines = source.splitlines()
    sites: list[ApplySite] = []
    for name, argument, scope in (("result", "values", indent), ("result2", "words", "    ")):
        start = lines.index(f"{scope}{name} = {argument}[0]") + 1
        statement = ast.parse(f"{name} = helper({argument})").body[0]
        assert isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
        sites.append(
            ApplySite(str(path), source, start, start + 1, scope, statement, statement.value)
        )
    shared_line = sites[0].end_line + 1
    assert shared_line == sites[1].start_line
    original_reveal = oracle.reveal
    batches: list[tuple[RevealRequest, ...]] = []

    def reveal(requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        batches.append(tuple(requests))
        positions = [(request.file_path, request.line) for request in requests]
        assert len(positions) == len(set(positions)), "Oracle keys omit indentation"
        results = dict(original_reveal(requests))
        if missing_result:
            for request in requests:
                if request.line == shared_line and "result" in request.expressions:
                    key = request.file_path, request.line, request.expressions.index("result")
                    assert key in results, "The real checker must first have supplied this evidence"
                    del results[key]
        return results

    monkeypatch.setattr(oracle, "reveal", reveal)
    rows = _signature_rows(sites, str(path), source, ("result",), oracle)
    assert [[ast.unparse(render_type(term)) for term in row] for row in rows] == (
        [] if missing_result else [["list[int]", "int"], ["list[str]", "str"]]
    )
    assert len(batches) == (2 if nested else 1)
    shared_probes = [
        request for batch in batches for request in batch if request.line == shared_line
    ]
    assert {request.indent for request in shared_probes} == {indent, "    "}
    assert path.read_text() == source
