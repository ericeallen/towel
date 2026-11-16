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

"""Project defaults, validated by the same actions that parse command-line options."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence, cast

from towel.project_layout import find_project_root, load_pyproject


class ConfigurationError(ValueError):
    """An invalid project policy, reported as an argument error."""


def _commands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return {
        name: command
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name, command in action.choices.items()
        if name in {"dry", "preview"}
    }


def _options(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {
        flag[2:]: action
        for action in parser._actions
        if not isinstance(action, (argparse._HelpAction, argparse._VersionAction))
        for flag in action.option_strings
        if flag.startswith("--") and flag != "--no-config"
    }


def _scalar(parser: argparse.ArgumentParser, action: argparse.Action, value: object) -> object:
    if type(value) not in (str, int, float):
        raise ValueError("expected a string or number of the option's type")
    # argparse applies its registered converter and choices, including custom
    # validators such as nonnegative counts and exclusion names.
    converted: object = parser._get_value(action, str(value))
    if type(value) is not type(converted) and not (type(value) is int and type(converted) is float):
        raise ValueError(f"expected {type(converted).__name__}, got {type(value).__name__}")
    parser._check_value(action, converted)
    return converted


def _value(
    parser: argparse.ArgumentParser, action: argparse.Action, key: str, value: object
) -> object:
    if isinstance(action, argparse.BooleanOptionalAction):
        if type(value) is not bool:
            raise ValueError("expected a boolean")
        return not value if key.startswith("no-") else value
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        if type(value) is not bool:
            raise ValueError("expected a boolean")
        return value if isinstance(action, argparse._StoreTrueAction) else not value
    if isinstance(action, argparse._AppendAction):
        if not isinstance(value, list):
            raise ValueError("expected an array")
        return [_scalar(parser, action, item) for item in value]
    return _scalar(parser, action, value)


def _explicit_arguments(parser: argparse.ArgumentParser, argv: Sequence[str]) -> set[str]:
    """Let argparse recognize explicit flags, including aliases and --option=value."""
    sparse = deepcopy(parser)
    for command in (sparse, *_commands(sparse).values()):
        command._defaults.clear()
        for action in command._actions:
            action.default = argparse.SUPPRESS
    return set(vars(sparse.parse_args(argv)))


def _validate(
    commands: Mapping[str, argparse.ArgumentParser], table: Mapping[str, object], path: Path
) -> dict[str, object]:
    options = {
        key: (parser, action)
        for parser in commands.values()
        for key, action in _options(parser).items()
    }
    accepted = ", ".join(sorted(options))
    defaults: dict[str, object] = {}
    keys: dict[str, str] = {}
    for key, value in table.items():
        try:
            if key not in options:
                raise ValueError("unknown option")
            parser, action = options[key]
            if action.dest in keys:
                raise ValueError(f"also set by {keys[action.dest]!r}")
            defaults[action.dest] = _value(parser, action, key, value)
            keys[action.dest] = key
        except (ValueError, argparse.ArgumentError) as error:
            raise ConfigurationError(
                f"{path}: [tool.towel] key {key!r}: {error}. Accepted keys: {accepted}"
            ) from error
    for parser in commands.values():
        for group in parser._mutually_exclusive_groups:
            selected = [
                keys[action.dest]
                for action in group._group_actions
                if action.dest in defaults and defaults[action.dest] not in (False, None)
            ]
            if len(selected) > 1:
                raise ConfigurationError(
                    f"{path}: [tool.towel] keys {', '.join(selected)} are mutually exclusive. "
                    f"Accepted keys: {accepted}"
                )
    return defaults


def apply_project_defaults(
    parser: argparse.ArgumentParser, args: argparse.Namespace, argv: Sequence[str]
) -> argparse.Namespace:
    """Return resolved arguments; explicit CLI options replace project defaults.

    The table accepts options of either command, so one project policy can
    configure both. Command-specific defaults apply only to that command.
    """
    result = argparse.Namespace(**vars(args), config_path=None)
    if args.command not in {"dry", "preview"} or args.no_config:
        return result
    target = args.input if args.command == "dry" else args.target
    root = find_project_root(Path(target))
    path = root / "pyproject.toml"
    try:
        project = load_pyproject(root, strict=True)
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"{path}: cannot read project configuration: {error}") from error
    tool: object = project.get("tool", {})
    if not isinstance(tool, dict) or "towel" not in tool:
        return result
    table: object = tool["towel"]
    commands = _commands(parser)
    # Prefer the selected command when an alias has command-specific semantics.
    command = commands.pop(args.command)
    commands[args.command] = command
    if not isinstance(table, dict):
        accepted = sorted({key for command in commands.values() for key in _options(command)})
        raise ConfigurationError(
            f"{path}: [tool.towel] must be a table. Accepted keys: {', '.join(accepted)}"
        )
    defaults = _validate(commands, cast(Mapping[str, object], table), path)
    explicit = _explicit_arguments(parser, argv)
    command = commands[args.command]
    for group in command._mutually_exclusive_groups:
        if any(action.dest in explicit for action in group._group_actions):
            explicit.update(action.dest for action in group._group_actions)
    for dest, value in defaults.items():
        if dest not in explicit and hasattr(result, dest):
            setattr(result, dest, value)
    result.config_path = str(path)
    return result
