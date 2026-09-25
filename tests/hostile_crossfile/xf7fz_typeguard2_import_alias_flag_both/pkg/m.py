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

import pkg
from pkg.f import make
from .compat import DEBUG as TYPE_CHECKING



def f1(k: int) -> int:
    x = make()
    if k > 100:
        return 0
    x.poke(k)
    n = x.size + k
    print("f1", n)
    return n


def f2(k: int) -> int:
    x = make()
    k = k * 2
    x.poke(k)
    n = x.size + k
    print("f2", n)
    return n * 2


if TYPE_CHECKING:
    MODE = 'alias-flag-on'
