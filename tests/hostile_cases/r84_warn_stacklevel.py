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
def w1(value):
    if value < 0:
        warnings.warn("negative", stacklevel=2)
    result = abs(value)
    print("w1", result)
    return result
def w2(value):
    if value < 0:
        warnings.warn("negative", stacklevel=2)
    result = abs(value)
    print("w2", result)
    return result
if __name__ == "__main__":
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        w1(-1); w2(-2)
    print([(str(c.message), c.filename.rsplit("/", 1)[-1]) for c in caught])
