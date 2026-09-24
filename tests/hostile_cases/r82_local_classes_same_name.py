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

def make_a():
    class Wrapper:
        def __init__(self): self.hits = 0
        def get(self, key):
            self.hits += 1
            if key is None:
                raise KeyError("none")
            return ("a", key, self.hits)
    return Wrapper()
def make_b():
    class Wrapper:
        def __init__(self): self.hits = 10
        def get(self, key):
            self.hits += 1
            if key is None:
                raise KeyError("none")
            return ("b", key, self.hits)
    return Wrapper()
if __name__ == "__main__":
    a, b = make_a(), make_b(); print(a.get(1), b.get(2), a.get(3))
