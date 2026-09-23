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

"""The project layout module moved to ``towel.project_layout``; this path re-exports it.

``ProjectLayout`` is gone since 1.772: module names come from the program's
own imports (``towel.import_model``), not from packaging metadata.
"""

from towel.project_layout import find_project_root, is_package_dir, load_pyproject

__all__ = ["find_project_root", "is_package_dir", "load_pyproject"]
