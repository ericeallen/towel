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

# In a class body the lambda's parameter __p is stored as _A__p, and the
# keyword __p= is passed as written, so the call raises TypeError. Neither
# method reads self, so a helper would be a module-level function, where
# nothing is mangled and the call returns 7.
class A:
    def m1(self, x):
        y = x + 1
        z = (lambda __p=0: 7)(__p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + 1
        z = (lambda __p=0: 7)(__p=y)
        print("m2", y, z)
        return z


if __name__ == "__main__":
    subject = A()
    for method in (subject.m1, subject.m2):
        try:
            print(method(1))
        except TypeError as error:
            print("raised", error)
