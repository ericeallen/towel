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

from pkg.one.introspect import IntrospectEndpoint
from pkg.one.revoke import RevocationEndpoint
from pkg.two.access import AccessEndpoint
from pkg.two.request import RequestEndpoint

for cls in (RevocationEndpoint, IntrospectEndpoint, RequestEndpoint, AccessEndpoint):
    endpoint = cls("validator", ("a", "b"))
    print(cls.__name__, endpoint.calls, endpoint.kinds, endpoint.label)
