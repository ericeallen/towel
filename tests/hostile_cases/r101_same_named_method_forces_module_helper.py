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


class Alpha:
    def build(self):
        owner = self
        owner.errors = []

        class First:
            def run(self):
                self.name = "a1"
                try:
                    emit(self, "x")
                except Exception as e:
                    owner.errors.append(e)
                    raise

        class Second:
            def run(self):
                self.name = "a2"
                try:
                    emit(self, "y")
                except Exception as e:
                    owner.errors.append(e)
                    raise

        return [First(), Second()]


class Beta:
    def build(self):
        return ["nothing here that matches the template at all", 1, 2, 3]


def emit(handler, body):
    log.append((handler.name, body))


if __name__ == "__main__":
    for handler in Alpha().build():
        handler.run()
    Beta().build()
    print(log)
