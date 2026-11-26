# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Towel's build backend: setuptools, with a source distribution that rebuilds byte for byte.

``pyproject.toml`` names this module, so ``python -m build``, ``uv build`` and
pip all go through it. With ``SOURCE_DATE_EPOCH`` set, two builds of one commit
by the same tool versions give identical sdists, as they already give identical
wheels. Without it the archive is as setuptools wrote it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from sdist_normalize import normalize_sdist, source_date_epoch
from setuptools import build_meta
from setuptools.build_meta import *  # noqa: F401,F403  (every other hook is setuptools')


def build_sdist(
    sdist_directory: str, config_settings: Optional[Mapping[str, object]] = None
) -> str:
    epoch = source_date_epoch(os.environ)  # a bad value fails before the build
    name: str = build_meta.build_sdist(sdist_directory, config_settings)
    if epoch is not None:
        normalize_sdist(Path(sdist_directory) / name, epoch)
    return name
