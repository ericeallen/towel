"""The warm pyright must answer exactly as the command line does.

Two properties here were live defects during development, and each would have
let an unsound refactoring through rather than merely slowing one down.

A change is judged by the files that depend on it. Pyright's in-memory overlays
reanalyze only the overlaid file, so a helper that breaks a consumer reads as
clean through them; the copy the server watches must therefore be a real one,
changed by real writes it is told about.

A file's diagnostics are stated once and then not repeated. A settle that
reported only what arrived during it would forget every error the project
already had, so a candidate checked later would look clean on a project that
never was.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from towel.type_inference import CheckSuccess, PyrightOracle


def _project(root: Path, *, returns: str = "int", value: str = "1") -> Path:
    """A provider and consumers that declare the provider's result type."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyrightconfig.json").write_text(
        '{"typeCheckingMode":"strict","include":["pkg"]}', encoding="utf-8"
    )
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    provider = package / "provider.py"
    provider.write_text(f"def make() -> {returns}:\n    return {value}\n", encoding="utf-8")
    for index in range(3):
        (package / f"consumer_{index}.py").write_text(
            "from pkg.provider import make\n\n\n"
            f"def use_{index}() -> int:\n    value: int = make()\n    return value\n",
            encoding="utf-8",
        )
    return provider


def _consumer_errors(errors: Sequence[object]) -> int:
    return sum(1 for error in errors if "consumer" in getattr(error, "path", ""))


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_a_helper_that_breaks_its_consumers_is_seen(tmp_path: Path, language_server: bool) -> None:
    """Both paths must report the consumers, not just the file that changed."""
    provider = _project(tmp_path)
    breaking = 'def make() -> str:\n    return "x"\n'
    oracle = PyrightOracle(language_server=language_server)
    try:
        clean = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
        broken = oracle.check_project({str(provider): breaking})
    finally:
        oracle.close()
    assert isinstance(clean, CheckSuccess) and isinstance(broken, CheckSuccess)
    assert _consumer_errors(clean.errors) == 0
    assert _consumer_errors(broken.errors) == 3, broken.errors
    assert provider.read_text(encoding="utf-8").endswith("return 1\n"), "the project was edited"


def test_a_candidate_is_judged_against_errors_the_project_already_had(tmp_path: Path) -> None:
    """Diagnostics stated before the first candidate must not be forgotten."""
    provider = _project(tmp_path, returns="str", value='"x"')
    oracle = PyrightOracle()
    try:
        # The first call absorbs the project's existing errors; the second
        # changes nothing and must still see them.
        first = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
        second = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert isinstance(first, CheckSuccess) and isinstance(second, CheckSuccess)
    assert _consumer_errors(first.errors) == 3
    assert _consumer_errors(second.errors) == 3, "a silent second call reported a clean project"


def test_the_warm_and_cold_paths_agree(tmp_path: Path) -> None:
    """Whatever the mechanism, the verdict is the checker's."""
    provider = _project(tmp_path)
    candidate = 'def make() -> str:\n    return "x"\n'
    verdicts = []
    for language_server in (True, False):
        oracle = PyrightOracle(language_server=language_server)
        try:
            result = oracle.check_project({str(provider): candidate})
        finally:
            oracle.close()
        assert isinstance(result, CheckSuccess)
        verdicts.append(sorted((Path(e.path).name, e.message) for e in result.errors))
    assert verdicts[0] == verdicts[1]


def test_a_stale_candidate_does_not_survive_into_the_next_check(tmp_path: Path) -> None:
    """The copy shows the candidate under test, never the one before it."""
    provider = _project(tmp_path)
    good = provider.read_text(encoding="utf-8")
    oracle = PyrightOracle()
    try:
        broken = oracle.check_project({str(provider): 'def make() -> str:\n    return "x"\n'})
        assert isinstance(broken, CheckSuccess)
        assert _consumer_errors(broken.errors) == 3
        # Naming no replacement at all must restore the project's own source.
        restored = oracle.check_project({str(provider): good})
        assert isinstance(restored, CheckSuccess)
        assert _consumer_errors(restored.errors) == 0, restored.errors
        again = oracle.check_project({})
        assert isinstance(again, CheckSuccess)
        assert _consumer_errors(again.errors) == 0, again.errors
    finally:
        oracle.close()


def test_a_session_that_cannot_start_still_yields_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable server falls back to the command line rather than failing."""
    provider = _project(tmp_path)
    monkeypatch.setattr(
        "towel.type_inference._pyright_langserver_command",
        lambda: ["/nonexistent/towel-no-such-langserver"],
    )
    oracle = PyrightOracle()
    try:
        result = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess)
    assert _consumer_errors(result.errors) == 0


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_a_candidate_is_judged_against_the_project_as_it_now_stands(
    tmp_path: Path, language_server: bool
) -> None:
    """An in-place run changes the project and supplies only what the next candidate alters.

    The warm copy once restored every unsupplied file to the bytes it first saw,
    so a valid follow-up was rejected and a breaking candidate accepted.
    """
    provider = _project(tmp_path)
    consumer = provider.with_name("consumer_0.py")
    oracle = PyrightOracle(language_server=language_server)
    try:
        baseline = oracle.check_project({str(consumer): consumer.read_text(encoding="utf-8")})
        # An applied refactoring gives the provider a helper, on disk.
        provider.write_text(
            "def make() -> int:\n    return extra()\n\n\ndef extra() -> int:\n    return 1\n",
            encoding="utf-8",
        )
        uses_the_helper = oracle.check_project(
            {
                str(consumer): "from pkg.provider import extra\n\n\n"
                "def use_0() -> int:\n    value: int = extra()\n    return value\n"
            }
        )
        # The provider's result type then changes on disk under an unchanged consumer.
        provider.write_text('def make() -> str:\n    return "x"\n', encoding="utf-8")
        now_broken = oracle.check_project({str(consumer): consumer.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert isinstance(baseline, CheckSuccess)
    assert isinstance(uses_the_helper, CheckSuccess)
    assert isinstance(now_broken, CheckSuccess)
    assert baseline.errors == ()
    assert uses_the_helper.errors == (), uses_the_helper.errors
    assert _consumer_errors(now_broken.errors) == 3, now_broken.errors
