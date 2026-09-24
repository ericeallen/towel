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

"""What a checker that looks at every line answers Towel's reachability probes with.

Towel asks every checker, before it accepts a change, whether it looks at the
lines the change writes (``towel.reachability``): a ``reveal_type`` of
:data:`~towel.reachability.PROBE` before each, which a checker answers exactly
where it does not take the code to be unreachable. A fake checker that answers
nothing looks at nothing, and every change it accepts is declined as
unverifiable, so the fakes that stand for a checker looking everywhere answer
the probes, and only the probes.
"""

from __future__ import annotations

from typing import Dict, Sequence

from towel.reachability import PROBE
from towel.type_inference import RevealKey, RevealRequest


def answer_probes(requests: Sequence[RevealRequest]) -> Dict[RevealKey, str]:
    """A type for every probe among ``requests``, as a checker that looks everywhere gives."""
    return {
        (request.file_path, request.line, index): "Literal[0]?"
        for request in requests
        for index, expression in enumerate(request.expressions)
        if expression == PROBE
    }


def only_probes(requests: Sequence[RevealRequest]) -> bool:
    """Whether ``requests`` ask where the checker looks, and nothing a helper's types need."""
    return bool(requests) and all(request.expressions == (PROBE,) for request in requests)
