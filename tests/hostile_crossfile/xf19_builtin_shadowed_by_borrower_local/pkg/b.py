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

# ``len`` is a local of fb; the same block in a.py reads the builtin, which
# a helper could only be given as a parameter, so the pair is declined.
def fb(values, len=lambda item: 40):
    print("pre", "b")
    size = len(values) + 1
    size = size * 2
    print("post", size)
    return size
