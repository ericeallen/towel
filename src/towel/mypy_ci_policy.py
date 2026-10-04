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

"""Literal mypy CLI policy in ordinary GitHub workflow jobs, without executing CI.

Only direct mypy / python -m mypy / uv run --frozen mypy commands are read.
Path targets and --strict are the supported policy; unsupported declarations
refuse verification rather than silently checking with weaker defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Optional, Sequence, Tuple


class UnsupportedMypyPolicy(ValueError):
    """A declared CI check cannot be represented by the supported literal policy."""


@dataclass(frozen=True)
class MypyCommand:
    strict: bool
    targets: Tuple[Path, ...]
    provenance: str


@dataclass(frozen=True)
class MypyPolicy:
    flags: Tuple[str, ...] = ()
    provenance: Tuple[str, ...] = ()
    targets: Tuple[Path, ...] = ()


def workflow_files(root: Path) -> Tuple[Path, ...]:
    return tuple(
        sorted(
            p
            for p in (root / ".github/workflows").glob("*")
            if p.is_file() and p.suffix in {".yml", ".yaml"}
        )
    )


def _scalar(value: str) -> str:
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise UnsupportedMypyPolicy("malformed YAML scalar")
        escapes = {
            "0": "\0",
            "a": "\a",
            "b": "\b",
            "t": "\t",
            "\t": "\t",
            "n": "\n",
            "v": "\v",
            "f": "\f",
            "r": "\r",
            "e": "\x1b",
            " ": " ",
            '"': '"',
            "\\": "\\",
            "/": "/",
            "N": "\x85",
            "_": "\xa0",
            "L": "\u2028",
            "P": "\u2029",
        }
        decoded: list[str] = []
        index = 1
        while index < len(value) - 1:
            char = value[index]
            if char == '"' or char in "\r\n":
                raise UnsupportedMypyPolicy("only single-line quoted YAML scalars are supported")
            if char != "\\":
                decoded.append(char)
                index += 1
                continue
            index += 1
            if index >= len(value) - 1:
                raise UnsupportedMypyPolicy("unterminated YAML escape")
            code = value[index]
            if code in escapes:
                decoded.append(escapes[code])
                index += 1
            elif code in {"x", "u", "U"}:
                width = {"x": 2, "u": 4, "U": 8}[code]
                digits = value[index + 1 : index + 1 + width]
                if len(digits) != width or re.fullmatch(r"[0-9A-Fa-f]+", digits) is None:
                    raise UnsupportedMypyPolicy("invalid Unicode YAML escape")
                number = int(digits, 16)
                if number > 0x10FFFF or 0xD800 <= number <= 0xDFFF:
                    raise UnsupportedMypyPolicy("invalid Unicode YAML code point")
                decoded.append(chr(number))
                index += width + 1
            else:
                raise UnsupportedMypyPolicy("unsupported YAML escape")
        return "".join(decoded)
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise UnsupportedMypyPolicy("malformed YAML scalar")
        return value[1:-1].replace("''", "'")
    return value


def _commentless(line: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif char in "\"'":
            quote = "" if quote == char else char if not quote else quote
        elif char == "#" and not quote and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _fields(lines: Sequence[str], indent: int) -> Tuple[Tuple[int, str, str], ...]:
    fields: list[Tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        if not line.strip() or _indent(line) != indent:
            continue
        match = re.fullmatch(r"\s*([A-Za-z_][\w-]*)\s*:\s*(.*)", line)
        if match is None or any(key == match[1] for _, key, _ in fields):
            raise UnsupportedMypyPolicy("ambiguous YAML fields")
        fields.append((index, match[1], match[2]))
    return tuple(fields)


def _mentions_shell_word(script: str, pattern: str) -> bool:
    # Shell quote concatenation and backslash quoting can spell a literal
    # checker/flag without its raw spelling. This classifies text only; the
    # standalone-command and YAML readers still decide whether it is supported.
    if re.search(pattern, script) is not None:
        return True
    try:
        decoded = " ".join(shlex.split(script, comments=True))
    except ValueError:
        return False
    return re.search(pattern, decoded) is not None


def _mentions_mypy(script: str) -> bool:
    return _mentions_shell_word(script, r"(?<![\w.-])mypy(?![\w.-])")


def _mentions_strict(script: str) -> bool:
    return _mentions_shell_word(script, r"(?<![\w-])--strict(?![\w-])")


def _quoted_end(text: str, at: int) -> Optional[int]:
    quote = text[at]
    index = at + 1
    while index < len(text):
        if quote == '"' and text[index] == "\\":
            index += 2
        elif text[index] == quote:
            if quote == "'" and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
            else:
                return index + 1
        else:
            index += 1
    return None


def _run_scripts(lines: Sequence[str]) -> Tuple[str, ...]:
    """Lexically classify run scalars even when their surrounding YAML is unsupported.

    This never interprets jobs, aliases or constructors. Decoding actual run
    text before structural validation prevents an encoded strict declaration
    from becoming weaker defaults when that validation refuses it.
    """
    found: list[str] = []
    for line_index, line in enumerate(lines):
        index = 0
        while index < len(line):
            char = line[index]
            previous = line[:index].strip()
            boundary = not previous or previous.endswith(("{", "[", ",")) or previous == "-"
            if char in "\"'":
                end = _quoted_end(line, index)
                if end is None:
                    break
                key = _scalar(line[index:end]) if boundary else ""
            elif boundary and line.startswith("run", index):
                end = index + 3
                key = "run"
            else:
                index += 1
                continue
            after = end
            while after < len(line) and line[after].isspace():
                after += 1
            if key != "run" or after >= len(line) or line[after] != ":":
                index = end
                continue
            value = line[after + 1 :].lstrip()
            # An anchor is recognized only to classify the literal scalar it
            # prefixes; structural validation still refuses all anchors.
            value = re.sub(r"^&[\w-]+\s+", "", value)
            body: list[str] = []
            for following in lines[line_index + 1 :]:
                if following.strip() and _indent(following) <= index:
                    break
                body.append(following.strip())
            if value.startswith(('"', "'")):
                quoted = _quoted_end(value, 0)
                if quoted is None:
                    # Classification only: multiline quoted values remain
                    # unsupported when the structural reader reaches them.
                    value = " ".join([value, *body])
                    quoted = _quoted_end(value, 0)
                if quoted is not None:
                    found.append(_scalar(value[:quoted]))
            elif value.startswith(("|", ">")):
                found.append("\n".join(body))
            else:
                found.append(" ".join([value, *body]))
            index = after + 1
    return tuple(found)


def _literal_shell_command(line: str) -> bool:
    """No command boundary, expansion or substitution outside literal quoting."""
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char in "\"'" and (not quote or quote == char):
            quote = "" if quote else char
        elif char == "#" and not quote:
            return index == 0 or line[index - 1].isspace()
        elif char in "$`" and quote != "'":
            return False
        elif char in ";|&<>(){}" and not quote:
            return False
    return not quote and not escaped


def _strict_in_script(script: str) -> bool:
    for line in script.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            tokens = []
        if tokens and tokens[0] in {"echo", "printf"} and _literal_shell_command(line):
            continue
        if _mentions_mypy(line) and _mentions_strict(line):
            return True
    return False


def _commands(script: str, root: Path, provenance: str) -> Tuple[MypyCommand, ...]:
    if not _mentions_mypy(script):
        return ()
    commands: list[MypyCommand] = []
    unsupported: list[str] = []
    for line in script.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            raise UnsupportedMypyPolicy(f"{provenance}: malformed shell command") from None
        if not tokens:
            continue
        literal = _literal_shell_command(line)
        installation = (
            tokens[:2] in (["pip", "install"], ["pip3", "install"])
            or (
                len(tokens) >= 5
                and re.fullmatch(r"python(?:3(?:\.\d+)?)?", tokens[0])
                and tokens[1:4] == ["-m", "pip", "install"]
            )
            or tokens[:3] == ["uv", "pip", "install"]
        )
        if literal and (installation or tokens[0] in {"echo", "printf"}):
            continue  # Not a checker invocation; other commands in the block still count.
        if tokens[:3] == ["uv", "run", "--frozen"]:
            tokens = tokens[3:]
        elif (
            len(tokens) >= 3
            and re.fullmatch(r"python(?:3(?:\.\d+)?)?", tokens[0])
            and tokens[1:3] == ["-m", "mypy"]
        ):
            tokens = tokens[2:]
        if not tokens or tokens[0] != "mypy" or not literal:
            unsupported.append(line)
            continue
        arguments = tokens[1:]
        strict = "--strict" in arguments
        targets: list[Path] = []
        for argument in arguments:
            if argument == "--strict":
                continue
            if argument.startswith("-") or not re.fullmatch(r"[\w./-]+", argument):
                if not strict:
                    break  # Other non-strict CI flags remain outside this narrow contract.
                raise UnsupportedMypyPolicy(f"{provenance}: unsupported mypy argument {argument!r}")
            target = (root / argument).resolve()
            if not target.is_relative_to(root.resolve()) or not target.exists():
                if not strict:
                    break
                raise UnsupportedMypyPolicy(
                    f"{provenance}: target is not an existing project path: {argument}"
                )
            targets.append(target)
        else:
            commands.append(MypyCommand(strict, tuple(targets), provenance))
        if strict and not targets:
            raise UnsupportedMypyPolicy(f"{provenance}: --strict requires explicit path targets")
    if (
        unsupported
        and _mentions_strict(script)
        and (commands or any(_mentions_mypy(line) for line in unsupported))
    ):
        raise UnsupportedMypyPolicy(
            f"{provenance}: only standalone literal mypy commands are supported"
        )
    return tuple(commands)


def _workflow_commands(path: Path, root: Path) -> Tuple[MypyCommand, ...]:
    text = path.read_text(encoding="utf-8")
    lines = [_commentless(line) for line in text.split("\n")]
    scripts = _run_scripts(lines)
    strict_candidate = any(_strict_in_script(script) for script in scripts)
    if not any(_mentions_mypy(script) for script in scripts):
        return ()
    found: list[MypyCommand] = []
    try:
        if "\t" in text:
            raise UnsupportedMypyPolicy("tab indentation")
        top = _fields(lines, 0)
        if any(re.search(r":\s*[&*!]", line) for line in lines):
            raise UnsupportedMypyPolicy(
                "YAML aliases, anchors and constructors are not interpreted"
            )
        jobs_at = next(
            (index + 1 for index, key, value in top if key == "jobs" and not value), None
        )
        if jobs_at is None:
            raise UnsupportedMypyPolicy("ordinary block jobs are required")
        jobs_end = next((index for index, _, _ in top if index >= jobs_at), len(lines))
        jobs = lines[jobs_at:jobs_end]
        indent = min((_indent(line) for line in jobs if line.strip()), default=0)
        fields = _fields(jobs, indent)
        for offset, (at, name, value) in enumerate(fields):
            stop = fields[offset + 1][0] if offset + 1 < len(fields) else len(jobs)
            body = jobs[at + 1 : stop]
            if not any(_mentions_mypy(script) for script in _run_scripts(body)):
                continue
            if value:
                raise UnsupportedMypyPolicy("ordinary block jobs are required")
            keys = _fields(body, indent + 2)
            job_keys = keys
            steps_at = next(
                (index + 1 for index, key, value in keys if key == "steps" and not value), None
            )
            if steps_at is None:
                raise UnsupportedMypyPolicy(f"job {name}: ordinary steps are required")
            steps_end = next((index for index, _, _ in keys if index >= steps_at), len(body))
            steps = body[steps_at:steps_end]
            step_indent = min((_indent(line) for line in steps if line.strip()), default=0)
            starts = [
                index
                for index, line in enumerate(steps)
                if _indent(line) == step_indent and line.lstrip().startswith("- ")
            ]
            for step, stop in zip(starts, [*starts[1:], len(steps)]):
                block = [
                    " " * (step_indent + 2) + steps[step].lstrip()[2:],
                    *steps[step + 1 : stop],
                ]
                if not any(_mentions_mypy(script) for script in _run_scripts(block)):
                    continue
                keys = _fields(block, step_indent + 2)
                provenance = f"{path.relative_to(root)} job {name} step {starts.index(step) + 1}"
                runs = [(index, value) for index, key, value in keys if key == "run"]
                if not runs:
                    continue  # e.g. an install step or a name mentioning mypy
                index, value = runs[0]
                provenance += f" line {jobs_at + at + 1 + steps_at + step + index + 1}"
                if value in {"|", "|-", "|+"}:
                    end = next((at for at, _, _ in keys if at > index), len(block))
                    script = "\n".join(line.strip() for line in block[index + 1 : end])
                else:
                    end = next((at for at, _, _ in keys if at > index), len(block))
                    if not value or any(line.strip() for line in block[index + 1 : end]):
                        raise UnsupportedMypyPolicy(
                            f"{provenance}: continued run scalar is unsupported"
                        )
                    if value.startswith(("|", ">", "&", "*", "!", "{", "[")) or "${{" in value:
                        raise UnsupportedMypyPolicy(
                            f"{provenance}: computed or unsupported run scalar"
                        )
                    script = _scalar(value)
                commands = _commands(script, root, provenance)
                if not commands:
                    continue
                contexts = (*top, *job_keys, *keys)
                unsupported_context = any(
                    key in {"defaults", "working-directory", "env"}
                    or (key == "if" and _scalar(value).lower() != "true")
                    or (key == "shell" and _scalar(value) not in {"bash", "sh"})
                    for _, key, value in contexts
                )
                if unsupported_context and not any(command.strict for command in commands):
                    continue
                if unsupported_context:
                    raise UnsupportedMypyPolicy(
                        f"{provenance}: conditional, custom directory/environment/shell"
                    )
                found.extend(commands)
    except UnsupportedMypyPolicy as error:
        if not strict_candidate:
            return ()
        raise UnsupportedMypyPolicy(f"{path.relative_to(root)}: {error}") from None
    return tuple(found)


def declared_mypy_root(path: Path) -> Optional[Path]:
    directory = (
        path.resolve().parent
        if path.suffix in {".py", ".pyi"} or path.is_file()
        else path.resolve()
    )
    for root in (directory, *directory.parents):
        for workflow in workflow_files(root):
            try:
                subject = path.resolve()
                if any(
                    command.strict
                    and any(
                        subject.is_relative_to(target)
                        or (subject.is_dir() and target.is_relative_to(subject))
                        for target in command.targets
                    )
                    for command in _workflow_commands(workflow, root)
                ):
                    return root
            except (UnsupportedMypyPolicy, OSError, UnicodeError):
                return root  # The build reports the unsupported declaration explicitly.
        if (root / ".git").exists() or (root / ".hg").exists():
            break
    return None


def mypy_policy(root: Path, paths: Sequence[Path]) -> MypyPolicy:
    declared = declared_mypy_root(root)
    if declared is None:
        return MypyPolicy()
    commands = tuple(
        command
        for path in workflow_files(declared)
        for command in _workflow_commands(path, declared)
    )
    policies: list[bool] = []
    provenance: list[str] = []
    targets: list[Path] = []
    for path in paths:
        matches = [
            command
            for command in commands
            if any(
                path.resolve().is_relative_to(target) for target in (command.targets or (declared,))
            )
        ]
        strictness = {command.strict for command in matches}
        if len(strictness) > 1:
            raise UnsupportedMypyPolicy(f"conflicting mypy CI policies for {path}")
        policies.append(strictness == {True})
        provenance.extend(command.provenance for command in matches)
        targets.extend(
            target for command in matches if command.strict for target in command.targets
        )
    if any(policies) and not all(policies):
        raise UnsupportedMypyPolicy(
            "one typed run spans strict CI targets and paths outside them; narrow the input"
        )
    if not any(policies):
        return MypyPolicy()  # Non-strict CI commands preserve configured/default behavior.
    return MypyPolicy(
        ("--strict",),
        tuple(dict.fromkeys(provenance)),
        tuple(dict.fromkeys(targets)),
    )
