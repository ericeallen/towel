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

# Functions defined after this line look builtins up in this dictionary,
# where ``len`` is not the builtin; no statement here binds ``len`` itself,
# but the pair is declined all the same.
import builtins

__builtins__ = dict(vars(builtins), len=lambda item: 7)


def fb(values):
    print("pre", "b")
    size = len(values) + 1
    size = size * 2
    print("post", size)
    return size
