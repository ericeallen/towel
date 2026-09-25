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

# In a class body the compiler rewrites an imported module's name and an
# imported member's name as it rewrites any private name: import __tool in
# class A imports _A__tool, and from tool import __member in class C takes
# _C__member. A module-level helper would import the names as written.
import sys
import types


class A:
    def m1(self, x):
        y = x + 1
        import __tool as z
        print("m1", y, z.NAME)
        return y

    def m2(self, x):
        y = x + 1
        import __tool as z
        print("m2", y, z.NAME)
        return y


class B:
    def m1(self, x):
        y = x + 1
        from __tool import NAME as z
        print("m1", y, z)
        return y

    def m2(self, x):
        y = x + 1
        from __tool import NAME as z
        print("m2", y, z)
        return y


class C:
    def m1(self, x):
        y = x + 1
        from tool import __member as z
        print("m1", y, z)
        return y

    def m2(self, x):
        y = x + 1
        from tool import __member as z
        print("m2", y, z)
        return y


def install(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    sys.modules[name] = module


if __name__ == "__main__":
    install("__tool", NAME="plain module")
    install("_A__tool", NAME="module mangled in A")
    install("_B__tool", NAME="module mangled in B")
    install("tool", __member="plain member", _C__member="member mangled in C")
    for subject in (A(), B(), C()):
        for method in (subject.m1, subject.m2):
            try:
                print(method(1))
            except ImportError as error:
                print("raised", type(error).__name__, error)
