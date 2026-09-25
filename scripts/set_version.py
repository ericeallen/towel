#!/usr/bin/env python3
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

"""Set a normalized PEP 440 release version, including alpha candidates."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from packaging.version import InvalidVersion, Version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        value = Version(args.version)
    except InvalidVersion as error:
        parser.error(str(error))
    if str(value) != args.version or value.local is not None:
        parser.error("Use a normalized public PEP 440 version, for example 1.1.0a1")
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    source = path.read_text(encoding="utf-8")
    updated, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{value}"', source)
    if count != 1:
        raise ValueError("Expected exactly one project version")
    path.write_text(updated, encoding="utf-8")
    print(f"Prepared version {value}; refresh uv.lock before building.")


if __name__ == "__main__":
    main()
