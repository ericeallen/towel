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

def first(scope, key):
    err = None
    try:
        cur = scope[key]
    except KeyError as e:
        err = ("missing", key)
    if err:
        raise ValueError(err)
    return cur
def second(target, spec):
    err = None
    try:
        ret = target[spec]
    except KeyError as e:
        err = ("absent", spec)
    if err:
        raise ValueError(err)
    return ret
if __name__ == "__main__":
    for f, arg in ((first, {"a": 1}), (second, {"b": 2})):
        print(f(arg, list(arg)[0]))
        try:
            f(arg, "zz")
        except ValueError as exc:
            print("caught", exc)
