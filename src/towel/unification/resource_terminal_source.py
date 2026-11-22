# Copyright 2026 Eric Allen
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

"""Recover import and assignment syntax without emitting speculative parse warnings."""

from __future__ import annotations

import ast
from io import StringIO
import tokenize
from unicodedata import normalize

from ..analysis_sources import _KNOWN_WARNING_SITES, parsing_is_pure
from ..parsing_warnings import _ordinary_raw_braces, _TOKENIZER_WARNING
from .resource_budget import ResourceBudget

_SHADOW_NAME = "_towel_terminal_name_"


def terminal_syntax(source: str, budget: ResourceBudget) -> ast.Module | None:
    """Return syntax with the same terminal bindings, or None when that cannot be proved.

    Comments and literal values contribute no import heads or dotted assignment
    origins. Interpolation can hide a walrus binding, so decline that case.
    Replace identifiers containing the 3.15 soft keyword with fresh identifiers
    and restore their normalized Python names on this parse's private AST.
    """
    budget.take(1 + len(source) // 64)
    if not _KNOWN_WARNING_SITES:
        return None
    if _TOKENIZER_WARNING.search(source):
        # A C tokenizer can warn on an escaped brace before yielding its token.
        safe = _ordinary_raw_braces(source)
        if safe is None:
            return None
        source = safe

    tokens: list[tuple[int, str]] = []
    aliases: dict[str, str] = {}
    interpolation = 0
    previous_lazy = False
    try:
        for token in tokenize.generate_tokens(StringIO(source).readline):
            kind, word = token.type, token.string
            label = tokenize.tok_name[kind]
            if label in {"FSTRING_START", "TSTRING_START"}:
                if not interpolation:
                    tokens.append((tokenize.STRING, '""'))
                interpolation += 1
                continue
            if label in {"FSTRING_END", "TSTRING_END"}:
                interpolation -= 1
                continue
            if kind == tokenize.ERRORTOKEN or (interpolation and ":=" in word):
                return None
            if interpolation or kind == tokenize.COMMENT:
                continue
            if kind == tokenize.STRING:
                # Before PEP 701 even the replacement expressions are part of this token.
                if ":=" in word and word[:2].lower().startswith(("f", "rf", "t", "rt")):
                    return None
                word = '""'
            if kind == tokenize.NAME:
                name = normalize("NFKC", word)
                if name.startswith(_SHADOW_NAME) or (previous_lazy and name in {"import", "from"}):
                    return None
                if "lazy" in word:
                    word = aliases.setdefault(name, f"{_SHADOW_NAME}{len(aliases)}")
                previous_lazy = name == "lazy"
            elif kind != tokenize.NL:
                previous_lazy = False
            tokens.append((kind, word))

        if interpolation:
            return None
        shadow = tokenize.untokenize(tokens)
        if not isinstance(shadow, str) or not parsing_is_pure(shadow):
            return None
        module = ast.parse(shadow)
    except (SyntaxError, tokenize.TokenError, UnicodeError, ValueError, RecursionError):
        return None

    if aliases:
        original = {shadow_name: name for name, shadow_name in aliases.items()}

        def dotted(name: str) -> str:
            return ".".join(original.get(part, part) for part in name.split("."))

        for node in ast.walk(module):
            if isinstance(node, ast.alias):
                node.name = dotted(node.name)
                if node.asname is not None:
                    node.asname = original.get(node.asname, node.asname)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                node.module = dotted(node.module)
            elif isinstance(node, ast.Name):
                node.id = original.get(node.id, node.id)
            elif isinstance(node, ast.Attribute):
                node.attr = original.get(node.attr, node.attr)
    return module
