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

# Every attribute normal lookup does not find is a field: rec.anything reads
# rec.fields["anything"]. The block the two methods share became a method
# helper named _extracted_func_0, which normal lookup then found first, so the
# field of that name was no longer served (audit r2_getattr). A class-private
# helper is stored as _Record__extracted_func_0, a name nothing here spells,
# and __getattr__ runs only when normal lookup fails.
class Record:
    """Unknown attributes are field lookups: rec.anything -> rec.fields['anything']."""

    def __init__(self, **fields):
        self.fields = fields
        self.log = []

    def __getattr__(self, name):
        try:
            return self.__dict__["fields"][name]
        except KeyError:
            raise AttributeError(name)

    def bump(self, key):
        print("bumping", key)
        self.log.append(("bump", key))
        value = self.fields.get(key, 0)
        self.fields[key] = value + 1
        return value

    def drop(self, key):
        print("dropping", key)
        self.log.append(("drop", key))
        value = self.fields.get(key, 0)
        self.fields[key] = value + 1
        return value


if __name__ == "__main__":
    prefixes = ("_extracted_func", "__extracted_func")
    names = [f"{prefix}_{number}" for prefix in prefixes for number in range(20)]
    record = Record(**{name: f"field {name}" for name in names})
    print(record.bump("a"), record.drop("a"), record.log)
    for name in names:
        value = getattr(record, name, None)
        print(name, value if isinstance(value, str) else type(value).__name__)
