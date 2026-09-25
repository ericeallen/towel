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

"""Run shop as its wheel ships it, without _devtools.py, then _devtools from the source tree."""
import os
import shutil
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
source = os.path.join(here, "pkg", "src")
with tempfile.TemporaryDirectory() as shipped:
    shutil.copytree(
        os.path.join(source, "shop"),
        os.path.join(shipped, "shop"),
        ignore=shutil.ignore_patterns("_devtools.py", "__pycache__"),
    )
    sys.path.insert(0, shipped)
    from shop.stats import describe

    print(describe([3, 1, 2]))
    for name in [name for name in sys.modules if name == "shop" or name.startswith("shop.")]:
        del sys.modules[name]
    sys.path[0] = source
    from shop._devtools import dump

    print(dump([5, 4]))
