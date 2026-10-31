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
# Gemini review of 2026-10-31, case nonlocal_in_class_method_hazard: a method
# declaring nonlocal state rebinds outer's state, not the class attribute of the
# same name, since a class body's names are invisible to the functions nested
# in it. f1 and f2 read state after calling the method, so passing state to a
# helper as an argument would read it before the call increments it.
def outer(x):
    state = 10

    class Counter:
        state = 0

        def bump(self):
            nonlocal state
            state += 1

    c = Counter()

    def f1(y):
        a = y + 1
        c.bump()
        b = a + state
        return b + 1

    def f2(y):
        a = y + 1
        c.bump()
        b = a + state
        return b + 2

    return f1(x), f2(x), Counter.state


# The read side (found probing the review's class_attr_shadows_global_lookup):
# a method's bare read of state finds outer's variable, which bump rebinds,
# not the class attribute.
def outer_read(x):
    state = 10

    def bump():
        nonlocal state
        state += 1

    class Worker:
        state = 0

        def f1(self, y):
            print("start", y)
            bump()
            print("read", state)
            return y + 1

        def f2(self, y):
            print("start", y)
            bump()
            print("read", 100)
            return y + 2

    w = Worker()
    return w.f1(x), w.f2(x)


if __name__ == "__main__":
    print(outer(5), outer(-3))
    print(outer_read(5))
