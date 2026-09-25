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

# The control for r7c_mangled_parameter_passed_by_keyword and
# r7c_mangled_parameter_passed_by_keyword_in_a_method_helper: without a private
# name the keyword binds the parameter wherever the code runs, and both pairs
# share a helper.
class A:
    def m1(self, x):
        y = x + 1
        z = (lambda p=0: 7)(p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + 1
        z = (lambda p=0: 7)(p=y)
        print("m2", y, z)
        return z


class B:
    def __init__(self):
        self.k = 1

    def m1(self, x):
        y = x + self.k
        z = (lambda p=0: 7)(p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + self.k
        z = (lambda p=0: 7)(p=y)
        print("m2", y, z)
        return z


if __name__ == "__main__":
    print(A().m1(1), A().m2(2), B().m1(3), B().m2(4))
