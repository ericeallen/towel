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

class Matcher:
    def __init__(self, test):
        self.test = test
    def compare_graph(self, num1, num2):
        if self.test == "graph":
            if num1 != num2:
                return False
        elif not num1 >= num2:
            return False
        if num1 < 0:
            return False
        return True
    def compare_edges(self, num1, num2):
        if self.test == "graph":
            if num1 != num2:
                return False
        elif not num1 >= num2:
            return False
        if num2 < 0:
            return False
        return True
if __name__ == "__main__":
    for test in ("graph", "mono"):
        m = Matcher(test)
        print(test, m.compare_graph(2, 2), m.compare_graph(3, 2), m.compare_edges(2, 2), m.compare_edges(-1, -2))
