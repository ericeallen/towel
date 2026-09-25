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

# Both methods import pkg.y.settings relatively. A method helper hosted
# in Base, in pkg.x, would import pkg.x.settings instead.
from ..x.base import Base


class Small(Base):
    def __init__(self, size):
        from .settings import LIMIT
        self.limit = size * LIMIT
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "s"


class Large(Base):
    def __init__(self, size):
        from .settings import LIMIT
        self.limit = size * LIMIT
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "l"
