# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

from .first import total_first

def total_second(values: list[int]) -> int:
    print('second')
    total = 0
    for value in values:
        if value > 1:
            # Double the positive contribution.
            total += value * 2  # noqa: E501
        else:
            total -= value
    return total
