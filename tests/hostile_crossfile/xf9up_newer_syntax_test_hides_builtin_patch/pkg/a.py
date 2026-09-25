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

# Round-4 audit P1-3, --cross-module: pkg.a and pkg.b share a block reading
# len, and the project's test patches len into pkg.b alone. The test is
# written for a newer Python than Towel runs on, which once made Towel skip it
# as a file that cannot run, host the helper in pkg.a, and so leave the patch
# nothing to reach: the project's own test failed. The run is refused before
# anything is written instead.
def describe_a(items):
    count = len(items)
    doubled = count * 2
    label = "n=" + str(doubled)
    print("a", label)
    return label
