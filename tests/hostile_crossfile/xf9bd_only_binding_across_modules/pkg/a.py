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

# The round-4 audit's P1-04 across modules: each block holds its function's
# only binding of total, which the function reads on its early return. A
# helper in either module would leave both functions reading their module's
# total where the originals raised UnboundLocalError.
total = "module total a"


def summarize_a(items):
    if not items:
        return ("empty", total)
    total = sum(items)
    total = total * 2
    print("doubled", total)
    return ("ok", len(items))
