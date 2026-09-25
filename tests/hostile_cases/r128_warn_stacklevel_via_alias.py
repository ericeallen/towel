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

import warnings
from warnings import warn as w
def wn1(v):
    log = [v]
    log.append(v * 2)
    w("careful", stacklevel=2)
    log.append(v * 3)
    return log
def wn2(v):
    log = [v]
    log.append(v * 2)
    w("careful", stacklevel=2)
    log.append(v * 3)
    return log + [2]
def wn3(v):
    log = [v]
    log.append(v * 2)
    warnings.warn("plain")
    log.append(v * 3)
    return log
def wn4(v):
    log = [v]
    log.append(v * 2)
    warnings.warn("plain")
    log.append(v * 3)
    return log + [4]
if __name__ == "__main__":
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        print(wn1(1), wn2(2), wn3(3), wn4(4))
    print([(str(c.message), c.lineno) for c in caught])
