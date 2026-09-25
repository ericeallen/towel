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

# line separator   in a comment
def f1(v, log):
    log.append("f1-pre-a")
    log.append("f1-pre-b")
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    acc.append(v * 4)
    log.append("f1-post-a")
    log.append("f1-post-b")
    return acc
def f2(v, log):
    log.append("f2-pre-a")
    log.append("f2-pre-b")
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    acc.append(v * 4)
    log.append("f2-post-a")
    log.append("f2-post-b")
    return acc
if __name__ == "__main__":
    log = []
    print(f1(1, log), f2(2, log), log)
