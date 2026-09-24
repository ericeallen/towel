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

import weakref
class T:
    pass
def id1():
    t = T()
    r = weakref.ref(t)
    alive = r() is t
    ident = id(t) == id(r())
    return alive, ident, r() is not None
def id2():
    t = T()
    r = weakref.ref(t)
    alive = r() is t
    ident = id(t) == id(r())
    return alive, ident, r() is not None, 2
def id3():
    t = T()
    r = weakref.ref(t)
    del t
    return r() is None
def id4():
    t = T()
    r = weakref.ref(t)
    del t
    return r() is None, 4
if __name__ == "__main__":
    print(id1(), id2(), id3(), id4())
