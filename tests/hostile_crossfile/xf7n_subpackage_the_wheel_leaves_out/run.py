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

"""Run shop as its wheel ships it, without shop.devtools, then the developer command from the tree."""
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
        ignore=shutil.ignore_patterns("devtools", "__pycache__"),
    )
    sys.path.insert(0, shipped)
    from shop.stats import describe
    from shop.cli import main

    print(describe([3, 1, 2]), main([]))
    for name in [name for name in sys.modules if name == "shop" or name.startswith("shop.")]:
        del sys.modules[name]
    sys.path[0] = source
    from shop.cli import main

    print(main(["dev"]))
