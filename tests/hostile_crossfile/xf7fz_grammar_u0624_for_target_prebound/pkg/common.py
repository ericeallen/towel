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

LOG = []
G = 5


def tr(tag, v):
    print("tr", tag, repr(v))
    LOG.append(tag)
    return v


class Box:
    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return f"Box({self.v!r})"

    def __eq__(self, other):
        return isinstance(other, Box) and self.v == other.v


class Ctx:
    def __init__(self, tag):
        self.tag = tag

    def __enter__(self):
        print("enter", self.tag)
        return self.tag

    def __exit__(self, et, ev, tb):
        print("exit", self.tag, et.__name__ if et else None)
        return False


def bump():
    global G
    G += 1
    return G
