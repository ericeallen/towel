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

# What a definition evaluates where it stands is a read of the caller's
# names: a lambda's or def's defaults and keyword-only defaults, and a def's
# annotations, run when the definition does. A helper not given those names
# raised NameError before its first call.
def lambda_default_first(k):
    print("lambda", 1)
    g = lambda v, s=k: v * s
    h = g(3)
    print(h, "one")
    return h
def lambda_default_second(k):
    print("lambda", 2)
    g = lambda v, s=k: v * s
    h = g(3)
    print(h, "two")
    return h
def nested_default_first(k, flag):
    print("nested", 1)
    if flag:
        g = lambda v: (lambda s=k: v * s)()
        h = g(4)
    else:
        h = None
    return h
def nested_default_second(k, flag):
    print("nested", 2)
    if flag:
        g = lambda v: (lambda s=k: v * s)()
        h = g(4)
    else:
        h = None
    return h
def def_heads_first(k, kind, flag):
    print("def", 1)
    if flag:
        def g(v: kind, s=k, *, t=k) -> kind:
            return kind(v * s + t)
        h = g(5)
    else:
        h = None
    return h
def def_heads_second(k, kind, flag):
    print("def", 2)
    if flag:
        def g(v: kind, s=k, *, t=k) -> kind:
            return kind(v * s + t)
        h = g(5)
    else:
        h = None
    return h
if __name__ == "__main__":
    print(lambda_default_first(5), lambda_default_second(7))
    print(nested_default_first(5, True), nested_default_second(7, True), nested_default_first(1, False))
    print(def_heads_first(2, float, True), def_heads_second(3, int, True), def_heads_first(2, int, False))
