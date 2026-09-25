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

# A del later in the loop's body unbinds x for the next iteration. Passed
# eagerly, the read would raise at the call, before the iteration's first
# print; the block raised only when it reached the read.
def f1(xs):
    x = 1
    for i in xs:
        print("start", i)
        print("value", x + 1)
        print("done", i)
        del x
    return 0
def f2(ys):
    x = 2
    for i in ys:
        print("start", i)
        print("value", x + 1)
        print("done", i)
        del x
    return 1
if __name__ == "__main__":
    for f in (f1, f2):
        for a in ([1], [1, 2]):
            try: print(f(a))
            except Exception as e: print(type(e).__name__, e)
