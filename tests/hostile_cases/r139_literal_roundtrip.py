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

def lit1(x):
    vals = ["\N{BULLET}", "\x00\t ", r"\d+\n", b"\xff\x00", "'\"", '"\'']
    vals += [1_000, 0x_FF, 0o17, 0b1010, 1e-9, 1e400, -0.0, 1j, 2.5e3j, 123456789012345678901234567890]
    vals += ["""tri
ple""", '''sin
gle''', "\\", "tab\tend", "\a\b\f\v\r"]
    vals.append(x)
    return vals
def lit2(x):
    vals = ["\N{BULLET}", "\x00\t ", r"\d+\n", b"\xff\x00", "'\"", '"\'']
    vals += [1_000, 0x_FF, 0o17, 0b1010, 1e-9, 1e400, -0.0, 1j, 2.5e3j, 123456789012345678901234567890]
    vals += ["""tri
ple""", '''sin
gle''', "\\", "tab\tend", "\a\b\f\v\r"]
    vals.append(x)
    return vals + [2]
if __name__ == "__main__":
    print(repr(lit1(1)), repr(lit2(2)))
