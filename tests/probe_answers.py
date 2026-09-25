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
from towel.type_baseline import IMPORT_PROBE_PREFIX
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
    """Whether ``requests`` ask where the checker looks, or what an import binds, and nothing else.

    Neither is a question a helper's types need.
    """
    return bool(requests) and all(
        request.expressions == (PROBE,)
        or all(expression.startswith(IMPORT_PROBE_PREFIX) for expression in request.expressions)
        for request in requests
    )
