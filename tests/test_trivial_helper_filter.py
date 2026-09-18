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


def test_assign_then_return_of_the_call_is_forwarding_too(tmp_path):
    # ``events = f(...)`` then ``return events`` is the same indirection as
    # ``return f(...)``. Two such helpers would otherwise pair with each other
    # and extract a third, without end, once a formatter wraps the call over
    # enough lines to pass the size gate.
    path = _write(
        tmp_path,
        "def first(a, b):\n"
        "    events = forward(a,\n"
        "                     b)\n"
        "    return events\n\n"
        "def second(a, b):\n"
        "    events = forward(a,\n"
        "                     b)\n"
        "    return events\n",
    )
    assert (
        UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False).analyze_file(path)
        == []
    )
    assert UnificationRefactorEngine(
        min_lines=3, reuse_existing_functions=False, skip_trivial_helpers=False
    ).analyze_file(path)


def test_unpacked_call_result_returned_as_a_tuple_is_forwarding(tmp_path):
    path = _write(
        tmp_path,
        "def first(a, b):\n"
        "    events, headers = forward(a,\n"
        "                              b)\n"
        "    return (events, headers)\n\n"
        "def second(a, b):\n"
        "    events, headers = forward(a,\n"
        "                              b)\n"
        "    return (events, headers)\n",
    )
    assert (
        UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False).analyze_file(path)
        == []
    )


def test_a_body_that_only_binds_literals_and_parameters_is_not_extracted(tmp_path):
    # ``a = 0; b = x; c = False`` followed by their use has no logic to share:
    # the helper would be a tuple of the same values with a longer call.
    path = _write(
        tmp_path,
        "def first(x):\n"
        "    depth = 0\n"
        "    seen = False\n"
        "    limit = x\n"
        "    return depth + limit if seen else limit\n\n"
        "def second(y):\n"
        "    depth = 0\n"
        "    seen = False\n"
        "    limit = y\n"
        "    return depth * limit if seen else limit\n",
    )
    assert (
        UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False).analyze_file(path)
        == []
    )
    assert UnificationRefactorEngine(
        min_lines=3, reuse_existing_functions=False, skip_trivial_helpers=False
    ).analyze_file(path)
