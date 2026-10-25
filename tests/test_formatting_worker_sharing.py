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

"""Per-file policies share run-owned workers, without sharing policy or failure state across runs."""

from concurrent.futures import ThreadPoolExecutor
import gc
import os
from pathlib import Path
import signal
import weakref

import pytest

from towel import formatting
from towel.project_tools import IsolatedFormatTool, ToolFailure


def _worker(callback: object) -> IsolatedFormatTool:
    context = formatting.formatting_repeatability(callback)
    assert context is not None and context.workers
    workers = {reference() for reference in context.workers}
    assert len(workers) == 1 and None not in workers
    worker = workers.pop()
    assert worker is not None and worker._process.poll() is None
    return worker


def test_black_shares_a_worker_across_settings_and_exclusions_until_last_callback_dies(
    tmp_path: Path,
) -> None:
    pytest.importorskip("black")
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.black]\nforce-exclude="skip.py$"\n')
    (nested / "pyproject.toml").write_text("[tool.black]\nskip-string-normalization=true\n")
    paths = [tmp_path / "first.py", tmp_path / "skip.py", nested / "inner.py", tmp_path / "last.py"]
    for path in paths:
        path.write_text("value = 'text'\n")
    factory = formatting.formatter_factory_for_project(tmp_path)
    expected = ['value = "text"', "value='text'", "value = 'text'", 'value = "text"']
    for path, output in zip(paths, expected):
        formatter = factory(str(path))
        assert formatter is not None and formatter("value='text'") == output
    worker = _worker(factory)
    process = worker._process
    reference = weakref.ref(worker)
    del worker, factory
    gc.collect()
    # A caller may keep a selected formatter after dropping the factory.
    assert formatter is not None
    assert process.poll() is None and formatter("value='text'") == 'value = "text"'
    del formatter
    gc.collect()
    assert reference() is None and process.wait(timeout=3) == 0


def test_concurrent_black_selection_is_bounded_and_factories_do_not_share_workers(
    tmp_path: Path,
) -> None:
    pytest.importorskip("black")
    (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
    paths = [tmp_path / f"module{index}.py" for index in range(6)]
    for path in paths:
        path.write_text("value = 1\n")
    first = formatting.formatter_factory_for_project(tmp_path)
    second = formatting.formatter_factory_for_project(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(first, map(str, paths)))
    assert second(str(paths[0])) is not None
    one, two = _worker(first), _worker(second)
    assert one is not two and one._process.pid != two._process.pid
    processes = one._process, two._process
    del one, two, first
    gc.collect()
    assert processes[0].wait(timeout=3) == 0 and processes[1].poll() is None
    del second
    gc.collect()
    assert processes[1].wait(timeout=3) == 0


def test_isort_shares_a_worker_across_file_policies_and_releases_it_with_the_factory(
    tmp_path: Path,
) -> None:
    pytest.importorskip("isort")
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.isort]\nskip=["skip.py"]\n')
    (nested / "pyproject.toml").write_text("[tool.isort]\nforce_single_line=true\n")
    paths = [tmp_path / "first.py", tmp_path / "skip.py", nested / "inner.py", tmp_path / "last.py"]
    original = "import sys\n\nvalue = 1\n"
    assembled = "from math import floor, ceil\nimport sys\n\nvalue = 1\n"
    sorted_source = "import sys\nfrom math import ceil, floor\n\nvalue = 1\n"
    split_source = "import sys\nfrom math import ceil\nfrom math import floor\n\nvalue = 1\n"
    for path in paths:
        path.write_text(original)
    finish = formatting.import_sorter_for_project(tmp_path).tool
    other = formatting.import_sorter_for_project(tmp_path).tool
    assert finish is not None and other is not None
    for path, expected in zip(paths, [sorted_source, assembled, split_source, sorted_source]):
        assert finish(str(path), assembled) == expected
    worker = _worker(finish)
    process = worker._process
    assert other(str(paths[0]), assembled) == sorted_source
    independent = _worker(other)
    assert independent is not worker
    reference = weakref.ref(worker)
    del worker, finish
    gc.collect()
    assert reference() is None and process.wait(timeout=3) == 0
    assert independent._process.poll() is None
    other_process = independent._process
    del independent, other
    gc.collect()
    assert other_process.wait(timeout=3) == 0


@pytest.mark.parametrize("name", ["black", "isort"])
def test_dead_shared_worker_cannot_be_hidden_by_selecting_another_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    pytest.importorskip(name)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.{name}]\n" + ('profile="black"\n' if name == "isort" else "")
    )
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    for path in [first, second]:
        path.write_text("value = 1\n")
    callback: formatting.SnippetFormatterFactory | formatting.FileFinisher
    if name == "black":
        factory = formatting.formatter_factory_for_project(tmp_path)
        formatter = factory(str(first))
        assert formatter is not None
        callback = factory
    else:
        finisher = formatting.import_sorter_for_project(tmp_path).tool
        assert finisher is not None
        assert finisher(str(first), first.read_text()) == first.read_text()
        callback = finisher
    worker = _worker(callback)
    worker._process.kill()
    worker._process.wait(timeout=3)
    assert formatting.formatting_repeatability(callback) is None

    def unexpected_worker(*args: object, **kwargs: object) -> None:
        raise AssertionError("Selecting another file must not restart a failed shared worker")

    monkeypatch.setattr(IsolatedFormatTool, "__init__", unexpected_worker)
    if name == "black":
        assert formatter is not None
        with pytest.raises(formatting.FormattingChangedCode):
            formatter("value=2")
        with pytest.raises(ToolFailure):
            factory(str(second))
    else:
        assert finisher is not None
        changed = "value = 2\n"
        assert finisher(str(first), changed) == changed
        assert finisher(str(second), changed) == changed
    assert worker._closed and formatting.formatting_repeatability(callback) is None


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_factory_rejects_access_before_acquiring_an_inherited_lock() -> None:
    tools = formatting._RunFormatTools()
    with tools._lock:
        pid = os.fork()
        if pid == 0:
            signal.alarm(2)
            try:
                tools.get("black")
            except ToolFailure:
                os._exit(0)
            except BaseException:
                os._exit(2)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
