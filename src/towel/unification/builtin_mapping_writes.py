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

"""Ordinary attribute writes that can replace another module's builtin mapping.

A receiver may be passed through arbitrary ordinary calls, so no alias analysis
can prove which module a store reaches. Any ``receiver.__builtins__`` store or
deletion in the program therefore makes every module's captured mapping uncertain.
Reflection is outside this scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import AbstractSet

from ..analysis_sources import parse_analysis_source
from ..consumers import MAXIMUM_FILES
from ..program_files import program_directories, refuse_unparsed_file
from ..source_text import decode_source
from .module_bindings import writes_builtin_mapping_attribute


def project_may_replace_builtin_mapping(
    root: Path, excluded_names: AbstractSet[str] = frozenset()
) -> bool:
    """Whether ordinary attribute writes anywhere in this program may replace a mapping.

    Excluded files still run and are read. An excluded file that cannot parse
    is treated as non-program input, as by the other whole-program scans.
    Unreadable files/directories and a project beyond the file limit provide
    no absence proof, so they conservatively return True. Nothing is retained;
    the engine takes one fresh snapshot per analysis before workers start.
    """
    uncertain = False

    def unreadable(_error: OSError) -> None:
        nonlocal uncertain
        uncertain = True

    count = 0
    for parent, names in program_directories(root, on_error=unreadable):
        for name in names:
            if not name.endswith(".py"):
                continue
            count += 1
            if count > MAXIMUM_FILES:
                return True
            # Keep visiting after a positive result: a later file that cannot
            # parse still receives the program's ordinary refusal/diagnostics.
            uncertain = _file_may_replace(Path(parent, name), root, excluded_names) or uncertain
    return uncertain


def _file_may_replace(path: Path, root: Path, excluded_names: AbstractSet[str]) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return True
    try:
        source = decode_source(data)
    except (UnicodeError, SyntaxError, LookupError):
        return False  # This source cannot run under its declared encoding.
    # An identifier is contiguous in ASCII source. Non-ASCII identifiers may
    # normalize to __builtins__, so those sources require the native AST.
    if source.isascii() and "__builtins__" not in source:
        return False
    try:
        tree = parse_analysis_source(source, filename=str(path))
    except (SyntaxError, ValueError, RecursionError) as error:
        refuse_unparsed_file(path, root, excluded_names, error)
        return False
    return writes_builtin_mapping_attribute(tree)
