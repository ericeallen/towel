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

class Group:
    def __init__(self):
        self.cmds = []
    def add(self, c):
        self.cmds.append(c)
    def command(self, *args, **kwargs):
        def decorator(f):
            cmd = ("command", f, args, kwargs)
            self.add(cmd)
            return cmd
        return decorator
    def group(self, *args, **kwargs):
        def decorator(f):
            cmd = ("group", f, args, kwargs)
            self.add(cmd)
            return cmd
        return decorator
if __name__ == "__main__":
    g = Group()
    print(g.command(1)("f1"))
    print(g.group(2, k=3)("f2"))
    print(g.cmds)
