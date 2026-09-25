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

# The star import binds only what common's __all__ lists, scale, so
# staticmethod and property are the builtins and the blocks under them may
# move (round-4 audit, P2-02: every decorator here was declined).
from .common import *


class Shapes:
    @staticmethod
    def square(k):
        n = scale(k) + 1
        m = n * n
        print("area", n, m)
        return m

    @staticmethod
    def cube(k):
        n = scale(k) + 1
        m = n * n
        print("area", n, m)
        return m * n

    @property
    def side(self):
        n = scale(2) + 1
        m = n * n
        print("area", n, m)
        return m - 1
