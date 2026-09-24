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

class Lexer:
    tokens = {}
class RubyLexer(Lexer):
    def gen_rules():
        states = {}
        for lbrace, rbrace, name in (("{", "}", "cb"), ("[", "]", "sb")):
            states[name + "-string"] = [(lbrace, "push"), (rbrace, "pop")]
            states.setdefault("strings", []).append((lbrace, name + "-string"))
            states[name + "-count"] = len(states)
        return states
    tokens = gen_rules()
    del gen_rules
class CrystalLexer(Lexer):
    def gen_rules():
        states = {}
        for lbrace, rbrace, name in (("(", ")", "pa"), ("<", ">", "ab")):
            states[name + "-string"] = [(lbrace, "push"), (rbrace, "pop")]
            states.setdefault("strings", []).append((lbrace, name + "-string"))
            states[name + "-count"] = len(states)
        return states
    tokens = gen_rules()
    del gen_rules
if __name__ == "__main__":
    print(RubyLexer.tokens)
    print(CrystalLexer.tokens)
