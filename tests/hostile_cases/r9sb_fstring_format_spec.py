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

# Round-4 P1-01: a name in an f-string's nested format specification is
# evaluated, so the helper must read its parameter there too; it kept the
# template's ``width`` and raised NameError at both sites.
def pad_value(width, value):
    text = f"[{value:>{width}}]"
    print("padded", text)
    return text.strip()


def pad_item(size, item):
    text = f"[{item:>{size}}]"
    print("padded", text)
    return text.strip()


def fill_value(width, fill, value):
    text = f"<{value:{fill}^{width}}>"
    print("filled", text)
    return text.strip()


def fill_item(size, pad, item):
    text = f"<{item:{pad}^{size}}>"
    print("filled", text)
    return text.strip()


if __name__ == "__main__":
    print(pad_value(6, "ab"), pad_item(4, "x"))
    print(fill_value(7, "*", "ab"), fill_item(5, "-", "x"))
