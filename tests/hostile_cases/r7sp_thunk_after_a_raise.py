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

# Nothing after a raise runs: the thunk in the dead code after it is never
# evaluated, and the raise itself comes first.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
def f1(o, e):
    raise e
    y = o.alpha()
    print("f1", y)
    return y
def f2(o, e):
    raise e
    y = o.beta() or 0
    print("f2", y)
    return y
if __name__ == "__main__":
    for f in (f1, f2):
        try:
            f(Loud(3), ValueError("boom"))
        except ValueError as error:
            print("ValueError", error)
