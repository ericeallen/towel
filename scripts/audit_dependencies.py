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

"""Export installed third-party versions for a strict, dependency-only audit.

Run in the environment being audited, after its frozen dependency sync. The
output includes every installed distribution except this project itself,
including transitive, development, optional, and editable dependencies. It is
an inventory, not a new dependency resolution. Only the standard library is
needed, so the exporter also runs in a bare wheel environment.
"""

from __future__ import annotations

from importlib.metadata import Distribution, distributions
import re
from typing import Iterable


def pinned_requirements(installed: Iterable[Distribution]) -> tuple[str, ...]:
    """Exact installed versions, refusing incomplete or ambiguous metadata."""
    versions: dict[str, str] = {}
    for distribution in installed:
        metadata = distribution.metadata
        name = metadata["Name"] if "Name" in metadata else None
        if (
            name is None
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name) is None
        ):
            raise ValueError(f"Invalid or missing installed distribution name: {name!r}")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical == "code-towel":
            # An unpublished local candidate has no registry release to audit.
            # Do not skip other editable or locally installed dependencies.
            continue
        version = distribution.version
        if not version or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version) is None:
            raise ValueError(f"Invalid or missing installed version for {name}: {version!r}")
        previous = versions.get(canonical)
        if previous is not None and previous != version:
            raise ValueError(
                f"Conflicting installed versions for {canonical}: {previous!r} and {version!r}"
            )
        versions[canonical] = version
    return tuple(f"{name}=={versions[name]}" for name in sorted(versions))


def main() -> None:
    try:
        requirements = pinned_requirements(distributions())
    except ValueError as error:
        raise SystemExit(f"Cannot collect audit dependencies: {error}") from error
    for requirement in requirements:
        print(requirement)


if __name__ == "__main__":
    main()
