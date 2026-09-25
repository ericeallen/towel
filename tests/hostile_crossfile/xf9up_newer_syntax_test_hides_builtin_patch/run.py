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

from pkg.a import describe_a
from pkg.b import describe_b

print(describe_a([1, 2]), describe_b([1, 2, 3]))
try:
    from tests import test_b
except SyntaxError as error:
    print("tests.test_b needs a newer Python:", type(error).__name__)
else:
    test_b.test_patched_len()
    print("patched")
