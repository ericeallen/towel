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

from .base import BaseEndpoint


class IntrospectEndpoint(BaseEndpoint):
    def __init__(self, validator, kinds):
        BaseEndpoint.__init__(self)
        self.validator = validator
        self.kinds = list(kinds)
        self.calls.append("introspect")
        self.label = "introspect"
