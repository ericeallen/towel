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

"""What the command line says about itself is what it does: its help examples."""

import argparse
import re
import shlex
from typing import Iterator, List

import pytest

from towel import cli

_EXAMPLE = re.compile(r"^\s*towel (?P<arguments>[^#]*?)\s*(#.*)?$")


def _parsers(parser: argparse.ArgumentParser) -> Iterator[argparse.ArgumentParser]:
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _parsers(child)


def _examples() -> List[str]:
    """Every ``towel ...`` line any help epilog shows, without placeholders like <command>."""
    return [
        match["arguments"]
        for parser in _parsers(cli._build_parser())
        for line in (parser.epilog or "").splitlines()
        if (match := _EXAMPLE.match(line)) and "<" not in match["arguments"]
    ]


def test_help_shows_examples_from_every_command() -> None:
    commands = {shlex.split(example)[0] for example in _examples()}
    assert {"dry", "preview", "rename-helpers"} <= commands


@pytest.mark.parametrize("example", _examples())
def test_every_help_example_is_a_valid_command_line(example: str) -> None:
    """``towel --help`` once showed ``towel rename-helpers`` without the target it requires."""
    try:
        cli._build_parser().parse_args(shlex.split(example))
    except SystemExit as error:
        pytest.fail(f"towel {example} is refused by the parser (exit {error.code})")
