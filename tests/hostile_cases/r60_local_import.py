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

def j1(data):
    import json
    text = json.dumps(data, sort_keys=True)
    size = len(text)
    print("j1", size)
    return json.loads(text), size
def j2(data):
    import json
    text = json.dumps(data, sort_keys=True)
    size = len(text)
    print("j2", size)
    return json.loads(text), size
if __name__ == "__main__":
    print(j1({"a": 1}), j2({"b": [1, 2]}))
