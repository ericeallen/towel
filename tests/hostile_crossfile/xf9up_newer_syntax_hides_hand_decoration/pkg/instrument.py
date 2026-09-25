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

import inspect
import textwrap


def instrumented(fn):
    """Recompile fn from its source with instrumentation, as typeguard and numba do."""
    source = textwrap.dedent(inspect.getsource(fn)).replace("i * 2", "i * 20")
    namespace = {}
    exec(compile(source, "<instrumented>", "exec"), fn.__globals__, namespace)
    return namespace[fn.__name__]
