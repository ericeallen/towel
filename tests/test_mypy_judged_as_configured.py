# Copyright 2025-2026 Eric Allen
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

"""Towel's mypy builds judge configurations, files and module names as mypy itself does.

A typed run promises that the project's own mypy, run as the project
configures it, reports no error the original did not have, and that a checker
which could not answer never reads as a clean verdict. The third release audit
found five ways the mypy worker judged something differently from mypy:

- D2: a configuration mypy only warns about refused the typed run;
- D4: a per-module ``follow_imports`` was ignored, so errors mypy reports were
  dropped and a helper it rejects was accepted;
- D8: a run over ``tests/`` found the project's package in a stale installed
  copy, not in ``src``, and wrote an annotation for the stale API;
- D9: probes named modules unlike mypy, every probe build of a PEP 420 project
  failed, and each failure read as code the checker does not look at;
- D10: a changed file outside ``files`` was given a module name its importer
  does not use, and mypy refused the build.

These tests read mypy's own option and module resolution through its API and
never build anything.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping, Optional, Sequence, Tuple, cast

import pytest

pytest.importorskip("mypy")

from mypy.errors import CompileError  # noqa: E402
from mypy.find_sources import create_source_list  # noqa: E402
from mypy.modulefinder import BuildSource, SearchPaths, compute_search_paths  # noqa: E402
from mypy import build as mypy_build  # noqa: E402
from mypy.build import BuildResult, default_data_dir  # noqa: E402
from mypy.options import Options  # noqa: E402

from towel import _mypy_worker as worker  # noqa: E402
from towel.reachability import PROBE, probe_plan  # noqa: E402
from towel.type_inference import (  # noqa: E402
    CheckFailure,
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    Revealed,
    RevealKey,
    RevealRequest,
    Subtyping,
    _BuildMessages,
    unanswered_files,
)
from towel.unification.exceptions import CheckerUnavailableError, RefactoringError  # noqa: E402
from towel.unification.refactor_engine import UnificationRefactorEngine  # noqa: E402
from tests.probe_answers import answer_probes  # noqa: E402


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _options(root: Path, config: str, *, probe: bool = False) -> Options:
    """The options the worker builds with for a project whose ``pyproject.toml`` holds ``config``."""
    (root / "pyproject.toml").write_text(config, encoding="utf-8")
    return worker._options(root, str(root / "pyproject.toml"), str(root / ".cache"), probe=probe)[0]


def _override(module: str, follow: str) -> str:
    return f'[[tool.mypy.overrides]]\nmodule = "{module}"\nfollow_imports = "{follow}"\n'


# --- D2: what mypy says while reading a configuration ------------------------


@pytest.mark.parametrize(
    "extra, said",
    [
        ("towel_no_such_option = true\n", "Unrecognized option: towel_no_such_option = True"),
        ('python_version = "3.4"\n', "python_version: Python 3.4 is not supported"),
        (
            '[[tool.mypy.overrides]]\nmodule = "pkg.a"\npython_version = "3.12"\n',
            "Per-module sections should only specify per-module flags (python_version)",
        ),
    ],
)
def test_what_mypy_only_warns_about_is_said_and_the_rest_of_the_configuration_applies(
    tmp_path: Path, extra: str, said: str
) -> None:
    """mypy prints each of these and checks on, exiting 0; so does a typed run (D2)."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n" + extra)
    configured = worker._read_configuration(str(tmp_path / "pyproject.toml"))
    assert any(said in line for line in configured.said), configured.said
    assert configured.options.disallow_untyped_defs


def test_a_configuration_mypy_reads_cleanly_says_nothing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    assert worker._read_configuration(str(tmp_path / "pyproject.toml")).said == ()


def test_a_configuration_mypy_will_not_start_from_is_refused_in_mypys_words(
    tmp_path: Path,
) -> None:
    """What stops mypy's own run stops the check, quoting mypy rather than an exit status."""
    with pytest.raises(ValueError, match="Cannot find config file"):
        worker._read_configuration(str(tmp_path / "missing.toml"))


def test_the_oracle_passes_on_each_configuration_warning_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    oracle = MypyInferrer()
    try:
        with caplog.at_level(logging.WARNING):
            oracle._pass_on(["an unknown option", "an unknown option", "a dropped version"])
            oracle._pass_on(["an unknown option"])
    finally:
        oracle.close()
    said = [record.getMessage() for record in caplog.records]
    assert said == [
        "mypy, reading the project's configuration: an unknown option",
        "mypy, reading the project's configuration: a dropped version",
    ]


@pytest.mark.parametrize(
    "answer, expected",
    [
        (
            {"messages": ["m.py:1: error: E"], "failure": None, "warnings": ["W"]},
            _BuildMessages(("m.py:1: error: E",), ("W",)),
        ),
        # A child that died answers without warnings at all.
        ({"messages": [], "failure": None}, _BuildMessages(())),
        (
            {"messages": [], "failure": None, "warnings": [1]},
            CheckFailure("mypy worker returned invalid configuration warnings"),
        ),
    ],
)
def test_the_worker_protocol_carries_configuration_warnings(
    answer: Mapping[str, object], expected: object
) -> None:
    reply = f"import sys; sys.stdin.readline(); print({json.dumps(json.dumps(answer))})"
    process = subprocess.Popen(
        [sys.executable, "-c", reply], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    oracle = MypyInferrer()
    try:
        assert oracle._exchange(process, {}) == expected
    finally:
        process.wait(timeout=10)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        oracle.close()


def test_the_worker_answer_carries_what_mypy_said_and_never_a_failure_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker, "_request", lambda request, cache: worker._Answered(["m"], ("warned",))
    )
    assert json.loads(worker._answer("{}", "cache")) == {
        "messages": ["m"],
        "failure": None,
        "warnings": ["warned"],
    }


# --- D9: how a probed module is named ------------------------------------------


NAMING = [
    # (files, [tool.mypy] body, probed file, module, directory it is found from)
    (["pkg/__init__.py", "pkg/m.py"], "", "pkg/m.py", "pkg.m", "."),
    (["src/pkg/__init__.py", "src/pkg/m.py"], "", "src/pkg/m.py", "pkg.m", "src"),
    (["src/pkg/__init__.py"], "", "src/pkg/__init__.py", "pkg", "src"),
    (["tests/test_m.py"], "", "tests/test_m.py", "test_m", "tests"),
    # A PEP 420 directory above a package: named from the package, or from the base.
    (
        ["src/acme/shop/__init__.py", "src/acme/shop/models.py"],
        "",
        "src/acme/shop/models.py",
        "shop.models",
        "src/acme",
    ),
    (
        ["src/acme/shop/__init__.py", "src/acme/shop/models.py"],
        'mypy_path = "src"\nexplicit_package_bases = true\n',
        "src/acme/shop/models.py",
        "acme.shop.models",
        "src",
    ),
    (
        ["tests/test_m.py"],
        "explicit_package_bases = true\n",
        "tests/test_m.py",
        "tests.test_m",
        ".",
    ),
    # A directory without ``__init__`` inside a package is a namespace package, unless turned off.
    (["pkg/__init__.py", "pkg/sub/m.py"], "", "pkg/sub/m.py", "pkg.sub.m", "."),
    (
        ["pkg/__init__.py", "pkg/sub/m.py"],
        "namespace_packages = false\n",
        "pkg/sub/m.py",
        "m",
        "pkg/sub",
    ),
    # A package directory whose name is no identifier: mypy names nothing there.
    (["cleaned-out/__init__.py", "cleaned-out/m.py"], "", "cleaned-out/m.py", "placeholder", "."),
]


@pytest.mark.parametrize("files, config, probed, module, directory", NAMING)
def test_a_probed_module_is_named_as_mypy_names_its_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: list[str],
    config: str,
    probed: str,
    module: str,
    directory: str,
) -> None:
    _write(tmp_path, {name: "" for name in files})
    monkeypatch.chdir(tmp_path)
    options = _options(tmp_path, "[tool.mypy]\n" + config, probe=True)
    path = str(tmp_path / probed)
    source = worker._probed_as_the_project_names(path, "", ("placeholder", str(tmp_path)), options)
    assert (source.module, Path(source.base_dir or "")) == (module, (tmp_path / directory))
    if module != "placeholder":
        # Exactly as mypy's own walk names the file for the project's run.
        [walked] = [
            each for each in create_source_list([str(tmp_path)], options) if each.path == path
        ]
        assert (walked.module, walked.base_dir) == (source.module, source.base_dir)


# --- D4: which files' errors count, per module --------------------------------


R, S, N = (worker._Followed.REPORTED, worker._Followed.SILENCED, worker._Followed.NOT_FOLLOWED)


@pytest.mark.parametrize(
    "config, module, suffix, expected",
    [
        ("", "tools.gen.x", ".py", R),
        ('follow_imports = "silent"\n', "tools.gen.x", ".py", S),
        ('follow_imports = "skip"\n', "tools.gen.x", ".py", N),
        ('follow_imports = "error"\n', "tools.gen.x", ".py", N),
        # D4: the module's own section decides, not the global setting.
        ('follow_imports = "silent"\n' + _override("tools.*", "normal"), "tools.gen.x", ".py", R),
        ('follow_imports = "silent"\n' + _override("tools.*", "normal"), "tools", ".py", R),
        ('follow_imports = "silent"\n' + _override("tools.*", "normal"), "app.main", ".py", S),
        (_override("tools.*", "skip"), "tools.gen.x", ".py", N),
        (_override("tools.*", "error"), "tools.gen.x", ".py", N),
        # A concrete section outranks a wildcard; an unstructured glob applies too.
        (
            _override("tools.*", "normal") + _override("tools.gen.x", "silent"),
            "tools.gen.x",
            ".py",
            S,
        ),
        ('follow_imports = "silent"\n' + _override("tools.*.x", "normal"), "tools.gen.x", ".py", R),
        ('follow_imports = "silent"\n' + _override("tools.*.x", "normal"), "tools.gen.y", ".py", S),
        # A stub is always followed, unless ``follow_imports_for_stubs`` says otherwise.
        (_override("tools.*", "skip"), "tools.gen.x", ".pyi", R),
        (
            "follow_imports_for_stubs = true\n" + _override("tools.*", "skip"),
            "tools.gen.x",
            ".pyi",
            N,
        ),
    ],
)
def test_an_import_is_followed_as_the_imported_modules_own_options_say(
    tmp_path: Path, config: str, module: str, suffix: str, expected: worker._Followed
) -> None:
    options = _options(tmp_path, "[tool.mypy]\n" + config)
    path = str(tmp_path / (module.replace(".", "/") + suffix))
    assert worker._followed(options, module, path) is expected


@dataclass(frozen=True)
class _Module:
    """A module of a finished build, as the judgement reads it."""

    dependencies: Tuple[str, ...] = ()
    ancestors: Optional[Tuple[str, ...]] = None
    path: Optional[str] = None


def _judged_messages(
    root: Path, config: str, graph: Mapping[str, _Module], unjudged: Sequence[str]
) -> list[str]:
    """What survives of one error per module when ``app.main`` alone is judged."""
    options = _options(root, "[tool.mypy]\n" + config)
    sources = [BuildSource(module.path, name) for name, module in graph.items() if module.path]
    messages = [f"{module.path}:1: error: in {name}" for name, module in graph.items()]
    kept = worker._as_the_project_judges(
        messages,
        graph,
        [source for source in sources if source.module in ("app.main", *unjudged)],
        lambda path: path.endswith("app/main.py"),
        options,
    )
    return [message.rsplit(" ", 1)[1] for message in kept]


def _graph(root: Path, **imports: Tuple[str, ...]) -> dict[str, _Module]:
    return {
        name: _Module(
            dependencies,
            tuple(".".join(name.split(".")[:end]) for end in range(1, name.count(".") + 1)),
            str(root / (name.replace(".", "/") + ".py")),
        )
        for name, dependencies in imports.items()
    }


@pytest.mark.parametrize(
    "config, kept",
    [
        ("", ["app.main", "tools.gen.x"]),
        ('follow_imports = "silent"\n', ["app.main"]),
        (
            'follow_imports = "silent"\n' + _override("tools.*", "normal"),
            ["app.main", "tools.gen.x"],
        ),
        (_override("tools.*", "skip"), ["app.main"]),
        (_override("tools.gen.x", "error"), ["app.main"]),
    ],
)
def test_a_changed_file_the_project_does_not_name_counts_where_its_import_is_followed(
    tmp_path: Path, config: str, kept: list[str]
) -> None:
    """``tools/gen/x.py`` is changed, outside ``files``, and imported by ``app/main.py`` (D4)."""
    graph = _graph(tmp_path, **{"app.main": ("tools.gen.x",), "tools.gen.x": ()})
    assert _judged_messages(tmp_path, config, graph, ["tools.gen.x"]) == kept


@pytest.mark.parametrize(
    "config, kept",
    [
        # Reached through a module whose errors are silenced, and reported itself.
        (_override("tools.a", "silent"), ["app.main", "tools.b"]),
        # Reached only through a module that is not followed at all.
        (_override("tools.a", "skip"), ["app.main"]),
    ],
)
def test_a_module_is_reached_through_silenced_imports_but_not_through_skipped_ones(
    tmp_path: Path, config: str, kept: list[str]
) -> None:
    graph = _graph(tmp_path, **{"app.main": ("tools.a",), "tools.a": ("tools.b",), "tools.b": ()})
    assert _judged_messages(tmp_path, config, graph, ["tools.a", "tools.b"]) == kept


BUILT = cast(BuildResult, object())


def test_a_complete_build_leaves_out_a_changed_file_the_project_does_not_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its importers must see ``Any``, as in the project's run, not the file's own types."""
    options = _options(tmp_path, "[tool.mypy]\n" + _override("tools.*", "skip"))
    built: list[list[str]] = []

    def build(sources: Sequence[BuildSource], options: Options) -> BuildResult:
        built.append([source.module for source in sources])
        return BUILT

    monkeypatch.setattr(mypy_build, "build", build)
    sources = [
        BuildSource(str(tmp_path / "app" / "main.py"), "app.main", None, str(tmp_path)),
        BuildSource(str(tmp_path / "tools" / "gen" / "x.py"), "tools.gen.x", "text", str(tmp_path)),
    ]
    for complete in (True, False):
        worker._build_as_the_project_reaches(
            sources, options, lambda path: "app" in path, complete=complete
        )
    assert built == [["app.main"], ["app.main", "tools.gen.x"]]


# --- D9: a probe build that failed is a checker failure --------------------------


def _stub_builds(monkeypatch: pytest.MonkeyPatch, oracle: MypyInferrer, answer: object) -> None:
    monkeypatch.setattr(oracle, "_build_errors", lambda *arguments, **keywords: answer)


def test_a_failed_probe_build_answers_nothing_and_names_what_it_could_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "m.py"
    request = RevealRequest(str(path), "x = 1\n", 1, "", (PROBE,))
    oracle = MypyInferrer()
    try:
        _stub_builds(monkeypatch, oracle, CheckFailure("the probe build failed"))
        failed = oracle.reveal([request])
        _stub_builds(monkeypatch, oracle, _BuildMessages(()))
        silent = oracle.reveal([request])
    finally:
        oracle.close()
    assert dict(failed) == {} and unanswered_files(failed) == {str(path): "the probe build failed"}
    assert dict(silent) == {} and unanswered_files(silent) == {}


def test_the_reachability_rule_reads_silence_only_from_a_build_that_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "def f() -> int:\n    return 1\n"
    path = str(tmp_path / "m.py")
    plan = probe_plan(text)
    assert plan is not None
    places = [place for place, _ in plan.spans]
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, annotate_helpers=False, type_oracle=oracle)
        _stub_builds(monkeypatch, oracle, _BuildMessages(()))
        # A completed build that answers no probe: the checker looks at none of it.
        assert engine._unlooked(oracle, {path: text}, {path: places}, every_checker=True) == {
            path: set(places)
        }
        _stub_builds(monkeypatch, oracle, CheckFailure("the probe build failed"))
        with pytest.raises(CheckerUnavailableError, match="(?s)could not build the probes.*failed"):
            engine._unlooked(oracle, {path: text}, {path: places}, every_checker=True)
    finally:
        oracle.close()


PAIR = "".join(
    f"def {name}(value: int) -> int:\n    total = value + 1\n    doubled = total * 2\n"
    f"    answer = doubled - {offset}\n    return answer\n\n\n"
    for name, offset in (("first", 3), ("second", 4))
)


class _ProbesFail:
    """A checker whose project checks pass and whose probe builds fail after the first ``kept``."""

    def __init__(self, kept: int) -> None:
        self.kept = kept

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        return CheckSuccess()

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        if self.kept > 0:
            self.kept -= 1
            return answer_probes(requests)
        return Revealed({}, {request.file_path: "the probe build failed" for request in requests})

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return [Subtyping.UNKNOWN for _ in pairs]

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "kept, refusal",
    [
        # The run-start map of what the checker looks at cannot be made: refused before anything.
        (0, r"(?s)Original project type check failed.*could not build the probes"),
        # Each change's own probes fail: every proposal is not judged, and the run says so.
        (1, r"(?s)checker could not run for \d+ proposal.*could not build the probes"),
    ],
)
def test_a_run_whose_probe_builds_fail_refuses_rather_than_call_everything_unreachable(
    tmp_path: Path, kept: int, refusal: str
) -> None:
    """D9 ended ``No refactorings found! Termination: fixed_point``, exit status 0."""
    (tmp_path / "m.py").write_text(PAIR)
    engine = UnificationRefactorEngine(
        min_lines=3,
        reuse_existing_functions=False,
        annotate_helpers=False,
        type_oracle=_ProbesFail(kept),
    )
    with pytest.raises(RefactoringError, match=refusal):
        engine.refactor_directory_to_fixed_point(str(tmp_path), str(tmp_path), progress="none")
    assert (tmp_path / "m.py").read_text() == PAIR


# --- D10: a changed file named as its importer names it ----------------------------


@pytest.mark.parametrize(
    "path, module, base",
    [
        ("/r/tools/gen/x.py", "tools.gen.x", "/r"),
        ("/r/tools/gen/x.py", "gen.x", "/r/tools"),
        ("/r/tools/gen/__init__.py", "tools.gen", "/r"),
        ("/r/tools/gen/x.pyi", "tools.gen.x", "/r"),
        ("/r/tools/gen/x.py", "other.x", None),
        ("/r/x.py", "a.b.x", None),
    ],
)
def test_the_directory_an_import_name_is_found_from(
    path: str, module: str, base: Optional[str]
) -> None:
    assert worker._base_for(path, module) == base


FOUND_TWICE = (
    '{path}: error: Source file found twice under different module names: "x" and "tools.gen.x"'
)


def _named_sources(root: Path) -> list[BuildSource]:
    return [
        BuildSource(str(root / "app" / "main.py"), "app.main", None, str(root)),
        BuildSource(str(root / "tools" / "gen" / "x.py"), "x", "text", str(root / "tools" / "gen")),
    ]


def test_a_file_the_project_reaches_only_by_import_takes_the_importers_name(tmp_path: Path) -> None:
    sources = _named_sources(tmp_path)
    message = FOUND_TWICE.format(path=tmp_path / "tools" / "gen" / "x.py")
    renamed = worker._named_by_its_importer(
        [message, "a note"], sources, lambda path: "app" in path, set()
    )
    assert renamed is not None
    assert [(source.module, source.base_dir, source.text) for source in renamed] == [
        ("app.main", str(tmp_path), None),
        ("tools.gen.x", str(tmp_path), "text"),
    ]


@pytest.mark.parametrize("judged, renamed_before", [(True, False), (False, True)])
def test_a_file_the_project_names_itself_or_one_already_renamed_keeps_the_refusal(
    tmp_path: Path, judged: bool, renamed_before: bool
) -> None:
    path = tmp_path / "tools" / "gen" / "x.py"
    renamed = {str(path)} if renamed_before else set()
    assert (
        worker._named_by_its_importer(
            [FOUND_TWICE.format(path=path)], _named_sources(tmp_path), lambda _: judged, renamed
        )
        is None
    )


"""What a stubbed build answers with: nothing here reads a build's result."""


def test_a_build_refused_for_a_file_found_twice_is_retried_under_the_importers_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[list[str]] = []

    def build(sources: Sequence[BuildSource], options: Options) -> BuildResult:
        built.append([source.module for source in sources])
        if len(built) == 1:
            raise CompileError([FOUND_TWICE.format(path=tmp_path / "tools" / "gen" / "x.py")])
        return BUILT

    monkeypatch.setattr(mypy_build, "build", build)
    result, sources = worker._build_as_the_project_reaches(
        _named_sources(tmp_path), Options(), lambda path: "app" in path, complete=False
    )
    assert result is BUILT
    assert built == [["app.main", "x"], ["app.main", "tools.gen.x"]]
    assert [source.module for source in sources] == ["app.main", "tools.gen.x"]


def test_a_refusal_no_rename_answers_is_raised_as_mypy_gave_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def build(sources: Sequence[BuildSource], options: Options) -> BuildResult:
        raise CompileError(["m.py:1: error: invalid syntax"])

    monkeypatch.setattr(mypy_build, "build", build)
    with pytest.raises(CompileError, match="invalid syntax"):
        worker._build_as_the_project_reaches(
            _named_sources(tmp_path), Options(), lambda _: False, complete=False
        )


# --- D8: what a build over tests/ alone searches ------------------------------


STALE_SHOP = {
    "shop/__init__.py": "",
    "shop/util.py": "DEFAULT: object = [1]\n",
    "shop/py.typed": "",
    "json5.py": "",
}
UNTYPED_SHOP = {"shop/__init__.py": "", "shop/util.py": "DEFAULT = [1]\n", "json5.py": ""}


def _src_layout(root: Path) -> None:
    _write(
        root,
        {
            "src/shop/__init__.py": "",
            "src/shop/util.py": "DEFAULT: list[int] = [1]\n",
            "tests/test_shop.py": "from shop.util import DEFAULT\n",
            # A lone module named like installed code is not the project's package.
            "examples/json5.py": "",
            # mypy's walk of the root cannot name this directory; the rest still counts.
            "docs/my-plugin/__init__.py": "",
        },
    )


def _search_with_installed(
    sources: Sequence[BuildSource], options: Options, installed: Sequence[Path]
) -> SearchPaths:
    """Where mypy searches for ``sources``' imports, with ``installed`` as the interpreter's code."""
    search = compute_search_paths(list(sources), options, default_data_dir())
    return SearchPaths(
        search.python_path,
        search.mypy_path,
        tuple(str(path) for path in installed),
        search.typeshed_path,
    )


@pytest.mark.parametrize(
    "installed, found",
    [
        # A stale non-editable copy would answer for ``shop``: the tree's ``src`` answers.
        ("a stale copy", ["src"]),
        # An untyped one would be an ``import-untyped`` error the project's run does not have.
        ("an untyped copy", ["src"]),
        # Nothing installed: nothing to correct, and nothing new is searched.
        ("nothing", []),
        # An editable install pointing into the tree already finds the tree's copy...
        ("the tree, typed", []),
        # ...unless it is untyped, which only installed code has to be.
        ("the tree, untyped", ["src"]),
    ],
)
def test_a_tests_only_target_finds_the_projects_package_in_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: str, found: list[str]
) -> None:
    project, site = tmp_path / "project", tmp_path / "site"
    _src_layout(project)
    _write(site / "typed", STALE_SHOP)
    _write(site / "untyped", UNTYPED_SHOP)
    places = {
        "a stale copy": [site / "typed"],
        "an untyped copy": [site / "untyped"],
        "nothing": [],
        "the tree, typed": [project / "src"],
        "the tree, untyped": [project / "src"],
    }[installed]
    if installed == "the tree, typed":
        (project / "src" / "shop" / "py.typed").write_text("")
    monkeypatch.chdir(project)
    options = _options(project, "[tool.mypy]\n")
    test = str(project / "tests" / "test_shop.py")
    targets = worker._complete_targets({test: ""}, options, project, [])
    assert targets == [test]  # the build walks the tests alone
    search = _search_with_installed(create_source_list(targets, options), options, places)
    run = worker._sources_of_the_projects_run(options, project)
    assert worker._where_installed_code_hides_the_project(search, run, options, project) == [
        str(project / name) for name in found
    ]


def test_a_build_that_already_searches_the_package_is_left_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, site = tmp_path / "project", tmp_path / "site"
    _src_layout(project)
    _write(site, STALE_SHOP)
    monkeypatch.chdir(project)
    options = _options(project, "[tool.mypy]\n")
    sources = create_source_list([str(project / "src" / "shop" / "util.py")], options)
    search = _search_with_installed(sources, options, [site])
    run = worker._sources_of_the_projects_run(options, project)
    assert worker._where_installed_code_hides_the_project(search, run, options, project) == []


def test_a_configured_files_list_is_the_projects_run_so_its_stale_answer_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``files = ["tests"]`` the project's own mypy finds the installed copy too."""
    project, site = tmp_path / "project", tmp_path / "site"
    _src_layout(project)
    _write(site, STALE_SHOP)
    monkeypatch.chdir(project)
    options = _options(project, '[tool.mypy]\nfiles = ["tests"]\n')
    sources = create_source_list(["tests"], options)
    search = _search_with_installed(sources, options, [site])
    run = worker._sources_of_the_projects_run(options, project)
    assert worker._where_installed_code_hides_the_project(search, run, options, project) == []
