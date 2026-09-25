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

from . import a
from .common import *


def f2(a, b, c, o):
    k1 = 3
    def inner():
        print("cell", k1)
        v1 = 6
        v2 = (v3 := c.get('k', 0)) + 1
        v4 = 0
        while v4 < 3:
            o.v = ((v1 and c.get('k', 0)) or max(a, v2))
            v4 += 1
        for v1 in b:
            print("it", v1)
        print("last", v1)
        print("after", v3, v4)
        return None
    return inner()
