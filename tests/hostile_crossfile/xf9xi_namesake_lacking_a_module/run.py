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

"""Run zzapp as it ships: its own package alone, beside the installed zzlib library."""
import os
import shutil
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
with tempfile.TemporaryDirectory() as shipped:
    shutil.copytree(os.path.join(here, "pkg", "zzapp"), os.path.join(shipped, "zzapp"))
    sys.path[:0] = [shipped, os.path.join(here, "site-packages")]
    import zzapp.cli

    print("main() ->", zzapp.cli.main())
