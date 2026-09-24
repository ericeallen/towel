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

def from_env(name, table):
    key = table.get(name)
    if key is None:
        msg = f"unknown name: {name}"
        raise ValueError(msg)
    value = table.get(key)
    if value is None:
        msg = f"unset key: {key}"
        raise KeyError(msg)
    return value
def from_registry(name, table):
    guid = table.get(name)
    if guid is None:
        msg = f"unknown name: {name}"
        raise ValueError(msg)
    found = table.get(guid, None)
    if found is None:
        msg = f"missing guid: {guid}"
        raise LookupError(msg)
    return found.upper()
if __name__ == "__main__":
    table = {"a": "b", "b": "c"}
    print(from_env("a", table), from_registry("a", {"a": "b", "b": "c"}))
    for f, arg in ((from_env, "zz"), (from_env, "b"), (from_registry, "zz"), (from_registry, "b")):
        try:
            print(f(arg, table))
        except (ValueError, KeyError, LookupError) as exc:
            print(type(exc).__name__, exc)
