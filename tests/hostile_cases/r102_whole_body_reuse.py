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

import math
def scale(items, factor):
    total = sum(items)
    scaled = [math.floor(i * factor / total) for i in items]
    print("scaled", scaled)
    return scaled
def rescale(values, k):
    total = sum(values)
    scaled = [math.floor(i * k / total) for i in values]
    print("scaled", scaled)
    return scaled
def summarize(data, weight, label):
    print(label)
    total = sum(data)
    scaled = [math.floor(i * weight / total) for i in data]
    print("scaled", scaled)
    return scaled
class Report:
    def render(self, data, weight):
        total = sum(data)
        scaled = [math.floor(i * weight / total) for i in data]
        print("scaled", scaled)
        return scaled
if __name__ == "__main__":
    print(scale([1, 2, 3], 10), rescale([4, 5], 3), summarize([6, 7], 2, "s"))
    print(Report().render([8, 9], 4))
