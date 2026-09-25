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

class Row:
    def __init__(self, items):
        self.items = items
    def children(self):
        def head(item, result):
            result.append(("row-head", item))
            result.append(("sep", 1))
            result.append(("text", item))
        def tail(item, result):
            result.append(("row-tail", item))
            result.append(("sep", 1))
            result.append(("text", item))
        out = []
        for item in self.items:
            head(item, out)
            tail(item, out)
        return out
class Column:
    def __init__(self, items):
        self.items = items
    def children(self):
        def head(item, result):
            result.append(("col-head", item))
            result.append(("sep", 1))
            result.append(("text", item))
        def tail(item, result):
            result.append(("col-tail", item))
            result.append(("sep", 1))
            result.append(("text", item))
        out = []
        for item in self.items:
            head(item, out)
            tail(item, out)
        return out
if __name__ == "__main__":
    print(Row(["a"]).children())
    print(Column(["b"]).children())
