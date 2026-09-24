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

def g1(fn):
    err = None
    try:
        ret = fn()
    except ValueError as e:
        err = e
    if err is not None:
        return "err:" + str(err)
    return ret + 1
def g2(fn):
    err = None
    try:
        ret = fn()
    except ValueError as e:
        err = e
    if err is not None:
        return "err:" + str(err)
    return ret + 2
if __name__ == "__main__":
    def bad(): raise ValueError("boom")
    print(g1(lambda: 1), g1(bad), g2(lambda: 2), g2(bad))
