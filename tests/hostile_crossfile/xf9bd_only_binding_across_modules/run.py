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

from pkg.a import summarize_a
from pkg.b import summarize_b

for function in (summarize_a, summarize_b):
    for argument in ([1, 2], []):
        try:
            print(function.__name__, argument, "->", repr(function(argument)))
        except Exception as error:
            print(function.__name__, argument, "raised", type(error).__name__, error)
