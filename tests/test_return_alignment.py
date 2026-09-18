"""One helper return serves call sites whose live variables are spelled differently."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine

SOURCE = """\
def f(items):
    result = []
    for item in items:
        result.append(item * 2)
    print(len(result))
    return result + [0]


def g(values):
    out = []
    for value in values:
        out.append(value * 2)
    print(len(out))
    return sorted(out)
"""


def test_each_site_receives_the_helper_result_under_its_own_name(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text(SOURCE)
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(source), str(tmp_path / "out"), max_iterations=0, progress="none"
        )
    assert reason == "fixed_point"
    assert sum(count for count, _ in results.values()) == 1
    rewritten = (tmp_path / "out" / "m.py").read_text()
    helper = rewritten.split("\n\n\n")[0]
    assert helper.endswith("    return result\n") or helper.endswith("    return result")
    assert "    result = __extracted_func_0(items)\n    return result + [0]\n" in rewritten
    assert "    out = __extracted_func_0(values)\n    return sorted(out)\n" in rewritten
