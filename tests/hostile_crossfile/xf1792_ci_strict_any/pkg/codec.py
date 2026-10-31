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

def encode(data: str) -> bytes:
    return data.encode()


def decode(data: bytes) -> str:
    return data.decode()


class Codec:
    def encode(self, data: str, errors: str = "strict") -> tuple[bytes, int]:
        if errors != "strict":
            raise ValueError("Unsupported errors")
        if not data:
            return (b"", 0)
        return (encode(data), len(data))

    def decode(self, data: bytes, errors: str = "strict") -> tuple[str, int]:
        if errors != "strict":
            raise ValueError("Unsupported errors")
        if not data:
            return ("", 0)
        return (decode(data), len(data))
