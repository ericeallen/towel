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

class Matcher:
	def __init__(self, patterns):
		self.patterns = patterns

	def match_a(self, file):
		include = None
		for index, pattern in enumerate(self.patterns):
			if pattern in file:
				include = index
		return include, "a"

	def match_b(self, file):
		include = None
		for index, pattern in enumerate(self.patterns):
			if pattern in file:
				include = index
		return include, "b"

if __name__ == "__main__":
	m = Matcher(["x", "y"]); print(m.match_a("xy"), m.match_b("zz"))
