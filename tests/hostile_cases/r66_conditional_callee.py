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

log = []
def ca(): log.append("ca"); return True
def cb(): log.append("cb"); return False
def q1(x):
    out = []
    out.append(x if ca() else -x)
    out.append(len(log))
    out.append("q1")
    return out
def q2(x):
    out = []
    out.append(x if cb() else -x)
    out.append(len(log))
    out.append("q2")
    return out
if __name__ == "__main__":
    print(q1(1), q2(2), log)
