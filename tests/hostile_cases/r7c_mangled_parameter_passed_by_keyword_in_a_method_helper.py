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

# As r7c_mangled_parameter_passed_by_keyword, but the methods read self, so a
# helper would be a method of A and keep the mangling. The call still raises,
# but its TypeError names the lambda by its qualified name, which would become
# A.__extracted_func_0.<locals>.
class A:
    def __init__(self):
        self.k = 1

    def m1(self, x):
        y = x + self.k
        z = (lambda __p=0: 7)(__p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + self.k
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
