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

# A for target, a match capture and a nested def each rebind a name bound
# before them. When the rebinding does not happen (an empty loop, a subject
# no case captures, an untaken branch), the later read sees the earlier
# binding; a helper that binds its own local instead reads it unbound.
def loop_one(xs):
    i = -1
    for i in xs:
        print("it", i)
    last = i * 2
    print("loop_one", last)
    return last
def loop_two(ys):
    i = -2
    for i in ys:
        print("it", i)
    last = i * 2
    print("loop_two", last)
    return last
def capture_one(v):
    x = -1
    match v:
        case [x]:
            print("one", x)
        case _:
            print("other")
    y = x * 2
    print("capture_one", y)
    return y
def capture_two(v):
    x = -2
    match v:
        case [x]:
            print("one", x)
        case _:
            print("other")
    y = x * 2
    print("capture_two", y)
    return y
def define_one(flag):
    def g():
        return "outer1"
    print("start", flag)
    if flag:
        def g():
            return "inner"
    r = g()
    print("define_one", r)
    return r
def define_two(flag):
    def g():
        return "outer2"
    print("start", flag)
    if flag:
        def g():
            return "inner"
    r = g()
    print("define_two", r)
    return r
if __name__ == "__main__":
    for function, arguments in (
        (loop_one, ([1, 2], [])),
        (loop_two, ([1, 2], [])),
        (capture_one, ([5], 3)),
        (capture_two, ([5], 3)),
        (define_one, (True, False)),
        (define_two, (True, False)),
    ):
        for argument in arguments:
            try:
                print(function.__name__, argument, "->", function(argument))
            except Exception as error:
                print(function.__name__, argument, "raised", type(error).__name__, error)
