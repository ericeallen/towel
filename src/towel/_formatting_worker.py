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

"""Format trusted tool text in an isolated process; never import a project tool."""

from __future__ import annotations

from contextlib import redirect_stdout
import importlib
import json
import os
from pathlib import Path
import posixpath
import re
import sys
import threading
import time
from typing import TYPE_CHECKING, Mapping, Optional, cast

if TYPE_CHECKING:
    import isort

MAX_FRAME_BYTES = 16 * 1024 * 1024


def _isort_skips(config: "isort.Config", root: Path, target: Path) -> bool:
    """Apply isort's file and ancestor selection as its project walk does."""
    try:
        relative = target.relative_to(root)
    except ValueError:
        return bool(config.is_skipped(target))
    named = {posixpath.normpath(entry) for entry in config.skips if not posixpath.isabs(entry)}
    return any(
        part.as_posix() in named or config.is_skipped(root / part)
        for part in (relative, *list(relative.parents)[:-1])
    )


def _string(request: Mapping[str, object], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str):
        raise ValueError(f"Expected {name} to be a string")
    return value


def _format(tool: str, request: Mapping[str, object]) -> Optional[str]:
    source = _string(request, "source")
    if tool == "black":
        import black

        length, normalize = request.get("line_length"), request.get("string_normalization")
        if type(length) is not int or length < 1 or type(normalize) is not bool:
            raise ValueError("Invalid Black settings")
        return black.format_str(
            source, mode=black.Mode(line_length=length, string_normalization=normalize)
        )
    import isort

    root, target = Path(_string(request, "root")), Path(_string(request, "path"))
    config = isort.Config(settings_path=str(root))
    if _isort_skips(config, root, target):
        return source
    try:
        return isort.code(source, config=config, file_path=target)
    except isort.exceptions.FileSkipped:
        return source


def _black_excludes(request: Mapping[str, object]) -> bool:
    from black import re_compile_maybe_verbose
    from black.const import DEFAULT_EXCLUDES
    from black.files import path_is_excluded

    root, path = Path(_string(request, "root")), Path(_string(request, "path"))
    try:
        parts = path.resolve().relative_to(root).parts
    except ValueError:
        return False
    patterns = []
    for name, default in (
        ("exclude", DEFAULT_EXCLUDES),
        ("extend-exclude", ""),
        ("force-exclude", ""),
    ):
        value = request.get(name)
        value = default if value is None else value
        if isinstance(value, str) and value:
            try:
                patterns.append(re_compile_maybe_verbose(value))
            except re.error:
                continue
    for depth in range(1, len(parts) + 1):
        relative = Path(*parts[:depth])
        normalized = "/" + relative.as_posix() + ("/" if (root / relative).is_dir() else "")
        if any(path_is_excluded(normalized, pattern) for pattern in patterns):
            return True
    return False


def _reply(value: Mapping[str, object]) -> None:
    raw = (json.dumps(value) + "\n").encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raw = b'{"failure":"Formatter response exceeds the byte limit"}\n'
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _exit_with_parent(owner: int) -> None:
    """End an orphan even when another forked process holds its input pipe."""
    while os.getppid() == owner:
        time.sleep(0.2)
    os._exit(1)


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in {"black", "isort"}:
        raise ValueError("Expected Black or isort")
    tool = sys.argv[1]
    threading.Thread(target=_exit_with_parent, args=(int(sys.argv[2]),), daemon=True).start()
    try:
        with redirect_stdout(sys.stderr):
            importlib.import_module(tool)
    except ImportError as error:
        _reply({"unavailable": str(error)})
        return
    _reply({"ready": True})
    while raw := sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1):
        if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
            _reply({"failure": "Invalid or oversized formatter request"})
            return
        try:
            value: object = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Expected a formatter request object")
            request = cast(Mapping[str, object], value)
            with redirect_stdout(sys.stderr):
                if tool == "black" and request.get("operation") == "excludes":
                    reply: Mapping[str, object] = {"excluded": _black_excludes(request)}
                else:
                    reply = {"source": _format(tool, request)}
        except Exception as error:  # External tool failures cannot certify generated text.
            _reply({"failure": f"{type(error).__name__}: {error}"})
        else:
            _reply(reply)


if __name__ == "__main__":
    main()
