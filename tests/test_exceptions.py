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

"""The exception hierarchy: a base, and one class per condition Towel raises."""

from towel.unification.exceptions import RefactoringError, TowelError, UnsupportedLayoutError


def test_every_towel_error_is_a_towel_error() -> None:
    assert issubclass(RefactoringError, TowelError)
    assert issubclass(UnsupportedLayoutError, TowelError)


def test_an_unsupported_layout_is_still_a_value_error_for_older_callers() -> None:
    assert issubclass(UnsupportedLayoutError, ValueError)
    try:
        raise UnsupportedLayoutError("hatch.toml")
    except ValueError as error:
        assert str(error) == "hatch.toml"
