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

"""The old import path for the project layout still resolves to the moved module."""

from __future__ import annotations

import towel.project_layout as current
import towel.unification.project_layout as shim


def test_the_unification_path_re_exports_the_project_layout_module() -> None:
    assert shim.find_project_root is current.find_project_root
    assert shim.is_package_dir is current.is_package_dir
    assert shim.load_pyproject is current.load_pyproject
    assert set(shim.__all__) == {"find_project_root", "is_package_dir", "load_pyproject"}
