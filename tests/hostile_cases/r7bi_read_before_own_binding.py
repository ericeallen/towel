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

# The block reads a name before binding it, so the name is local to its
# function throughout and the read raises UnboundLocalError. Passed from the
# call site, where the block no longer binds it, the name would find the
# module's function and succeed, or raise NameError.
def scale(x):
    return x * 2
def f1(xs):
    print("start", len(xs))
    scale = scale(len(xs))
    print("after", scale)
    return scale
def f2(xs):
    print("begin", len(xs) * 2)
    scale = scale(len(xs))
    print("after", scale)
    return scale
def g1(a):
    print("start", a)
    label = len(str(label))
    print("after", label)
    return label
def g2(a):
    print("begin", a * 2)
    label = len(str(label))
    print("after", label)
    return label
if __name__ == "__main__":
    for function in (f1, f2, g1, g2):
        try:
            print(function.__name__, "->", function([1, 2]))
        except Exception as error:
            print(function.__name__, "raised", type(error).__name__, error)
