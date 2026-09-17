"""The default skips trivial forwarding helpers (a lone raise, a return of a call).

Size does not separate these from useful extractions -- a passthrough that
forwards many arguments is verbose yet worthless -- so the filter is structural.
skip_trivial_helpers=False restores the old behavior for callers that want every
duplicate, however small.
"""

from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine


def _write(tmp_path: Path, code: str) -> str:
    path = tmp_path / "m.py"
    path.write_text(code)
    return str(path)


def test_passthrough_return_is_skipped_by_default(tmp_path):
    path = _write(
        tmp_path,
        "def first(value):\n"
        "    return forward(value, 1, 2, 3)\n\n"
        "def second(value):\n"
        "    return forward(value, 1, 2, 3)\n",
    )
    assert UnificationRefactorEngine(min_lines=1).analyze_file(path) == []
    assert UnificationRefactorEngine(min_lines=1, skip_trivial_helpers=False).analyze_file(path)


def test_lone_raise_is_skipped_by_default(tmp_path):
    path = _write(
        tmp_path,
        "def first(value):\n"
        "    raise ValueError(value)\n\n"
        "def second(value):\n"
        "    raise ValueError(value)\n",
    )
    assert UnificationRefactorEngine(min_lines=1).analyze_file(path) == []
    assert UnificationRefactorEngine(min_lines=1, skip_trivial_helpers=False).analyze_file(path)


def test_real_two_statement_body_is_still_extracted(tmp_path):
    # A genuine shared computation is NOT trivial forwarding and stays proposed.
    path = _write(
        tmp_path,
        "def first(value):\n"
        "    tmp = value + 1\n"
        "    return tmp * 2\n\n"
        "def second(value):\n"
        "    tmp = value + 1\n"
        "    return tmp * 2\n",
    )
    assert UnificationRefactorEngine(min_lines=1).analyze_file(path)
