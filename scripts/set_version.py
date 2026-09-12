#!/usr/bin/env python3
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
