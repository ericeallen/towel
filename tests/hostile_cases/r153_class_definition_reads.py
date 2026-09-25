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

# A class statement evaluates its decorators, bases and keywords where it
# stands, and its body runs there at once, reading the caller's names; a
# method, a lambda or a comprehension element inside it looks past the
# class's own names to the caller's. The helper must receive each.
def tag(cls):
    cls.tagged = True
    return cls
class Meta(type):
    pass
def build_first(k, base, meta, deco, flag):
    print("build", "one")
    if flag:
        @deco
        class C(base, metaclass=meta):
            limit = k
            twice = [k * i for i in range(2)]
            def get(self):
                return k + self.limit
        h = (C().get(), C.twice, type(C).__name__, getattr(C, "tagged", False), C.__mro__[1].__name__)
    else:
        h = None
    return h
def build_second(k, base, meta, deco, flag):
    print("build", "two")
    if flag:
        @deco
        class C(base, metaclass=meta):
            limit = k
            twice = [k * i for i in range(2)]
            def get(self):
                return k + self.limit
        h = (C().get(), C.twice, type(C).__name__, getattr(C, "tagged", False), C.__mro__[1].__name__)
    else:
        h = None
    return h
# The class binds k, yet its method and its comprehension element read the
# caller's k. (Declined today: the class's k counts as the function's.)
def shadow_first(k, flag):
    print("shadow", "one")
    if flag:
        class D:
            k = 100
            inner = [k for _ in range(1)]
            def get(self):
                return k
        h = (D().get(), D.k, D.inner)
    else:
        h = None
    return h
def shadow_second(k, flag):
    print("shadow", "two")
    if flag:
        class D:
            k = 100
            inner = [k for _ in range(1)]
            def get(self):
                return k
        h = (D().get(), D.k, D.inner)
    else:
        h = None
    return h
if __name__ == "__main__":
    print(build_first(1, object, Meta, tag, True), build_second(2, dict, type, tag, True))
    print(shadow_first(5, True), shadow_second(7, True))
