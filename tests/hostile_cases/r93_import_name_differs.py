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

import sys
import types
sys.modules["r93mod"] = types.SimpleNamespace(alpha="A", beta="B", gamma="C")
import r93mod
class Recorder:
    def __init__(self, message):
        self.message = message
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
class Tests:
    def record(self, message):
        return Recorder(message)
    def check(self, left, right):
        print("check", left, right, left == right)
    def test_alpha(self):
        message = "loading alpha"
        with self.record(message) as w:
            from r93mod import alpha
        self.check(alpha, r93mod.alpha)
        self.check(w.message, message)
    def test_beta(self):
        message = "loading beta"
        with self.record(message) as w:
            from r93mod import beta
        self.check(beta, r93mod.beta)
        self.check(w.message, message)
    def test_gamma(self):
        message = "loading gamma"
        with self.record(message) as w:
            from r93mod import gamma
        self.check(gamma, r93mod.gamma)
        self.check(w.message, message)
if __name__ == "__main__":
    t = Tests()
    t.test_alpha()
    t.test_beta()
    t.test_gamma()
