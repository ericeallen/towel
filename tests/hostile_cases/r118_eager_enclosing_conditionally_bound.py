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

def outer(ready):
    if ready:
        opt_a = 1
        opt_b = 2
    def f(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(opt_a)
        total = sum(acc) + len(acc)
        return total
    def g(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(opt_b)
        total = sum(acc) + len(acc)
        return total * 2
    return f(False, [1, 2]), g(False, [3])
if __name__ == "__main__":
    print(outer(False))
