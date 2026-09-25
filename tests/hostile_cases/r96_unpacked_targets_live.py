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

def lookup(table, name):
    return table.get(name), [name] * 2
def first(table, name, log):
    from json import dumps
    frame, stmts = lookup(table, name)
    if not stmts:
        raise KeyError(name)
    log.append(dumps(frame))
    return frame, stmts
def second(table, name, log):
    from json import dumps
    frame, stmts = lookup(table, name)
    if not stmts:
        raise KeyError(name)
    log.extend([dumps(s) for s in stmts])
    return stmts, frame
if __name__ == "__main__":
    log = []
    print(first({"a": 1}, "a", log), second({"b": 2}, "b", log), log)
