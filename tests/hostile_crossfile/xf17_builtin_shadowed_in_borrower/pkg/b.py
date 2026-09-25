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

# ``len`` is this module's own function; a.py reads the builtin. A helper
# hosted in a.py that read ``len`` bare, in the lambda and the comprehension
# too, would measure with the builtin for this module's call, and no helper
# takes a builtin as a parameter, so the pair is declined.
def len(item):
    return 99


def fb(values):
    print("pre", "b")
    measure = lambda item: len(item)
    sizes = [len(str(v)) for v in values] + [measure(values)]
    sizes = sizes * 2
    print("post", sizes)
    return sizes
