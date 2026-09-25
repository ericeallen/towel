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

# Neither module imports the other, and both print at import time: hosting
# a helper in either would make importing one run the other's print.
print("importing b")
def build(xs):
    out = []
    for x in xs:
        out.append(x * 2)
        out.append(x * 3)
    out.append(len(out))
    return out
