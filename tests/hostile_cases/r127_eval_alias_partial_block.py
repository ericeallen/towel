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

e = eval
x_ = exec
import builtins as bi
def ev1(a, b):
    total = a + b
    doubled = total * 2
    r = e("total + doubled")
    s = str(r) + "x"
    return r, s
def ev2(a, b):
    total = a + b
    doubled = total * 2
    r = e("total + doubled")
    s = repr(r)
    return r, s, 2
def bv1(a, b):
    total = a + b
    doubled = total * 2
    r = bi.eval("total + doubled")
    s = str(r) + "x"
    return r, s
def bv2(a, b):
    total = a + b
    doubled = total * 2
    r = bi.eval("total + doubled")
    s = repr(r)
    return r, s, 2
def lv1(a, b):
    total = a + b
    doubled = total * 2
    r = sorted(bi.locals())
    s = str(r) + "x"
    return r, s
def lv2(a, b):
    total = a + b
    doubled = total * 2
    r = sorted(bi.locals())
    s = repr(r)
    return r, s, 2
def xv1(a, b):
    total = a + b
    doubled = total * 2
    x_("total = doubled + 1")
    s = str(total) + "x"
    return total, doubled, s
def xv2(a, b):
    total = a + b
    doubled = total * 2
    x_("total = doubled + 1")
    s = repr(total)
    return total, doubled, s, 2
if __name__ == "__main__":
    print(ev1(1, 2), ev2(3, 4), bv1(1, 2), bv2(3, 4), lv1(1, 2), lv2(3, 4), xv1(1, 2), xv2(3, 4))
