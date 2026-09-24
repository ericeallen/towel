"""The mypy worker's cost per request must not depend on how many it has served.

Successive ``build.build`` calls in one process accumulate every rechecked
module's tree, and each build opens with a full collection over all of it. On
Sphinx that made a run quadratic: 117 recorded requests took 855 s served in
one process and 88 s with each build in its own forked child.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType

import pytest

from towel import type_inference

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

WORKER = Path(type_inference.__file__).with_name("_mypy_worker.py")


def _worker_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_towel_mypy_worker_under_test", WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve(requests: list[str], tmp_path: Path) -> tuple[int, list[dict[str, object]]]:
    """Run the worker's loop over ``requests`` with a build that reports its own process."""
    driver = f"""import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("worker", {str(WORKER)!r})
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

def build(request, cache):
    if request == "die":
        os._exit(3)
    return worker._Answered([str(os.getpid())], ())

worker._request = build
sys.stderr.write(str(os.getpid()))
sys.argv = ["worker", {str(tmp_path)!r}]
worker.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", driver],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stderr), [json.loads(line) for line in result.stdout.splitlines()]


def test_each_build_runs_in_a_process_that_does_not_outlive_it(tmp_path: Path) -> None:
    server, answers = _serve(["a", "b", "c"], tmp_path)
    builders = [int(answer["messages"][0]) for answer in answers]  # type: ignore[index]
    assert len(builders) == 3
    assert server not in builders
    assert len(set(builders)) == 3


def test_a_build_that_dies_is_reported_and_the_worker_keeps_serving(tmp_path: Path) -> None:
    _, answers = _serve(["a", "die", "b"], tmp_path)
    assert [answer["failure"] is None for answer in answers] == [True, False, True]
    assert "wait status" in str(answers[1]["failure"])
    assert answers[1]["messages"] == []


def test_text_a_file_already_holds_is_withheld_until_that_path_has_differed(
    tmp_path: Path,
) -> None:
    worker = _worker_module()
    cache = tmp_path / "cache"
    cache.mkdir()
    path, absent = str(tmp_path / "m.py"), str(tmp_path / "absent.py")
    Path(path).write_text("x: int = 1\n", encoding="utf-8")
    assert worker._text_mypy_must_be_given({path: "x: int = 1\n"}, str(cache)) == {}
    changed = {path: "x: int = 2\n", absent: "y = 0\n"}
    assert worker._text_mypy_must_be_given(changed, str(cache)) == changed
    # The cache entry now describes the changed text under the file's mtime and size.
    assert worker._text_mypy_must_be_given({path: "x: int = 1\n"}, str(cache)) == {
        path: "x: int = 1\n"
    }
    # A replaced worker inherits the cache, so it must inherit the record with it.
    assert _worker_module()._text_mypy_must_be_given({path: "x: int = 1\n"}, str(cache)) == {
        path: "x: int = 1\n"
    }


def test_a_verdict_follows_the_supplied_text_and_never_a_cache_entry_written_from_other_text(
    tmp_path: Path,
) -> None:
    """Withholding the restored text let mypy answer for the file with the broken text's entry."""
    package = tmp_path / "project"
    package.mkdir()
    (package / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    provider, consumer = package / "provider.py", package / "consumer.py"
    clean = "def make() -> int:\n    return 1\n"
    provider.write_text(clean, encoding="utf-8")
    consumer_text = "from provider import make\n\nvalue: int = make()\n"
    consumer.write_text(consumer_text, encoding="utf-8")
    checker = type_inference.MypyInferrer()
    try:
        unchanged = {str(provider): clean, str(consumer): consumer_text}
        broken = {**unchanged, str(provider): "def make() -> str:\n    return ''\n"}
        for sources, expected_errors in [(unchanged, 0), (broken, 1), (unchanged, 0), (broken, 1)]:
            result = checker.check_project(sources)
            assert isinstance(result, type_inference.CheckSuccess), result
            assert len(result.errors) == expected_errors, result.errors
    finally:
        checker.close()


def test_a_record_cut_short_by_an_interrupted_append_is_forgiven_and_repaired(
    tmp_path: Path,
) -> None:
    """The path is recorded before its build, so a cut append means that build never ran."""
    worker = _worker_module()
    cache = tmp_path / "cache"
    cache.mkdir()
    first, second = str(tmp_path / "first.py"), str(tmp_path / "second.py")
    assert worker._text_mypy_must_be_given({first: "x = 1\n"}, str(cache)) == {first: "x = 1\n"}
    record = cache / worker._SUPPLIED_TEXT_RECORD
    record.write_bytes(record.read_bytes() + b'"/cut/sho')
    assert worker._recorded_paths(record) == {first}
    assert worker._text_mypy_must_be_given({second: "y = 1\n"}, str(cache)) == {second: "y = 1\n"}
    assert worker._recorded_paths(record) == {first, second}
    assert b"/cut/sho" not in record.read_bytes()


def test_damage_inside_the_record_is_an_error_not_a_guess(tmp_path: Path) -> None:
    worker = _worker_module()
    record = tmp_path / worker._SUPPLIED_TEXT_RECORD
    record.write_text('"/a.py"\nnot json\n"/b.py"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        worker._recorded_paths(record)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stopping_the_worker_stops_the_build_it_was_waiting_for(tmp_path: Path) -> None:
    """A timed-out request terminates the worker; its build must not run on unowned.

    A forgotten build once held a core and 7 GB for 25 minutes and confounded a
    measurement. The owner terminates the worker exactly as ``_stop_worker`` does.
    """
    started = tmp_path / "build.pid"
    driver = f"""import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("worker", {str(WORKER)!r})
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

def build(request, cache):
    with open({str(started)!r}, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(120)
    return []

worker._request = build
sys.argv = ["worker", {str(tmp_path)!r}]
worker.main()
"""
    server = subprocess.Popen(
        [sys.executable, "-c", driver], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    try:
        assert server.stdin is not None
        server.stdin.write(b'"a request"\n')
        server.stdin.flush()
        deadline = time.monotonic() + 30
        while not (started.is_file() and started.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        build = int(started.read_text())
        assert build != server.pid and _alive(build)
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=10)
        deadline = time.monotonic() + 10
        while _alive(build) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(build), "the build outlived the worker that was told to stop"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
        for stream in (server.stdin, server.stdout):
            if stream is not None:
                stream.close()


def test_a_build_does_not_outlive_a_worker_that_was_killed_outright(tmp_path: Path) -> None:
    """SIGKILL cannot be passed on, so the build has to notice for itself."""
    started = tmp_path / "build.pid"
    driver = f"""import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("worker", {str(WORKER)!r})
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker._ORPHAN_CHECK_SECONDS = 0.05

def build(request, cache):
    with open({str(started)!r}, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(120)
    return []

worker._request = build
sys.argv = ["worker", {str(tmp_path)!r}]
worker.main()
"""
    server = subprocess.Popen(
        [sys.executable, "-c", driver], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    try:
        assert server.stdin is not None
        server.stdin.write(b'"a request"\n')
        server.stdin.flush()
        deadline = time.monotonic() + 30
        while not (started.is_file() and started.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        build = int(started.read_text())
        assert _alive(build)
        server.kill()
        server.wait(timeout=10)
        deadline = time.monotonic() + 15
        while _alive(build) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(build), "the build outlived the worker that was killed"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
        for stream in (server.stdin, server.stdout):
            if stream is not None:
                stream.close()
