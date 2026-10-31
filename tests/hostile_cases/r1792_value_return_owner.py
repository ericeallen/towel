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

events = []
class Resource:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        events.append(self.name)
def fail():
    raise ValueError('expected')
def first(unused):
    inner = Resource('inner:first')
    value = 3
    return value
def second(unused):
    inner = Resource('inner:second')
    value = 3
    return value
first(Resource('outer:first'))
second(Resource('outer:second'))
print(events)
