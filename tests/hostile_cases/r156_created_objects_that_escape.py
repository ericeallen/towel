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

# Escaped callables retain their closure values and keyword-call interfaces.
# Their reflective names, signatures and source locations are not observed.
import collections
def make_first(k):
    print("make", 1)
    g = lambda: k + 1
    print(g())
    return g
def make_second(k):
    print("make", 2)
    g = lambda: k + 2
    print(g())
    return g
def scale_first(k):
    print("scale", 1)
    g = lambda value: value * k
    print(g(3) + 1)
    return g
def scale_second(k):
    print("scale", 2)
    g = lambda other: other * k
    print(g(3) + 1)
    return g
def bind_first(items):
    print("bind", 1)
    kept = []
    for j in items:
        kept.append(lambda j=j: j * 2)
    return kept
def bind_second(items):
    print("bind", 2)
    kept = []
    for i in items:
        kept.append(lambda i=i: i * 2)
    return kept
def lazy_first(items):
    print("lazy", 1)
    doubled = (item * 2 for item in items)
    print("made")
    return doubled
def lazy_second(items):
    print("lazy", 2)
    doubled = (item * 3 for item in items)
    print("made")
    return doubled
def counter_first(words):
    print("count", 1)
    counts = collections.defaultdict(lambda: 0)
    for word in words:
        counts[word] += 1
    return counts
def counter_second(words):
    print("count", 2)
    counts = collections.defaultdict(lambda: 0)
    for word in words:
        counts[word] += 2
    return counts
def inner_first(k, flag):
    print("inner", 1)
    if flag:
        def step(v):
            return v + k
        result = step
    else:
        result = None
    return result
def inner_second(k, flag):
    print("inner", 2)
    if flag:
        def step(v):
            return v + k
        result = step
    else:
        result = None
    return result
if __name__ == "__main__":
    print(make_first(1)(), make_second(1)())
    for call in (lambda: scale_first(2)(value=1), lambda: scale_second(2)(other=1)):
        try:
            print(call())
        except TypeError as error:
            print("TypeError", error)
    print([f() for f in bind_first([1, 2]) + bind_second([3])])
    print([f(j=7) for f in bind_first([1, 2])], [f(i=8) for f in bind_second([3])])
    print(list(lazy_first([1])), list(lazy_second([2])))
    print(counter_first(["a", "a"]).default_factory(), dict(counter_second(["b"])))
    print(inner_first(1, True)(4), inner_second(2, True)(5))
