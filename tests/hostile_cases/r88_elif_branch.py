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

def render(volatile, autoescape, out):
    if volatile:
        out.append("(escape if ctx else str)(")
    elif autoescape:
        out.append("escape(")
        out.append("#")
    else:
        out.append("str(")
        out.append("#")
    return out
def render2(volatile, autoescape, out):
    if volatile:
        out.append("dyn(")
    elif autoescape:
        out.append("escape(")
        out.append("#")
    else:
        out.append("str(")
        out.append("#")
    return out
if __name__ == "__main__":
    for f in (render, render2):
        for v in (True, False):
            for a in (True, False):
                print(f.__name__, v, a, f(v, a, []))
