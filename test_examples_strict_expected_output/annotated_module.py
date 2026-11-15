"""
Annotated code: PEP 526 variable annotations, annotated signatures with
keyword-only parameters, a decorated (dataclass) class and absolute stdlib
imports.

The duplicated blocks assign annotated locals, so the extracted helpers
carry the annotations the sites declared; the keyword-only parameters and
the frozen dataclass are the surrounding code the helpers must still fit.
"""

from dataclasses import dataclass
from typing import Final

SCALE: Final[float] = 100.0
EMPTY_LABEL: Final[str] = "empty"


@dataclass(frozen=True)
class Summary:
    """The figures one pass over a series produces."""

    label: str
    total: float
    peak: float
    count: int


def summarize_values(values: list[float], *, label: str = "values", floor: float = 0) -> Summary:
    """Total, peak and count of the values at or above ``floor``."""
    total: float = 0.0
    peak: float = float(floor)
    count: int = 0
    for value in values:
        if value < floor:
            continue
        total += value
        peak = max(peak, value)
        count += 1
    if count == 0:
        return Summary(EMPTY_LABEL, 0.0, 0.0, 0)
    return Summary(label, total, peak, count)


def summarize_scaled(values: list[float], *, label: str = "scaled", floor: float = 0) -> Summary:
    """The same figures after scaling every value by ``SCALE``."""
    total: float = 0.0
    peak: float = float(floor)
    count: int = 0
    for value in values:
        if value < floor:
            continue
        total += value
        peak = max(peak, value)
        count += 1
    if count == 0:
        return Summary(EMPTY_LABEL, 0.0, 0.0, 0)
    return Summary(label, total * SCALE, peak * SCALE, count)


def describe_counts(items: list[object], *, width: int = 8) -> list[str]:
    """A fixed-width line per distinct item, most frequent first."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        key: str = str(item)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    order.sort(key=lambda key: (-counts[key], key))
    return [f"{key:<{width}}{counts[key]}" for key in order]


def describe_shares(items: list[object], *, width: int = 8) -> list[str]:
    """A fixed-width line per distinct item with its share of the whole."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        key: str = str(item)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    order.sort(key=lambda key: (-counts[key], key))
    total = sum(counts.values())
    return [f"{key:<{width}}{counts[key] / total:.2f}" for key in order]


class Ledger:
    """Entries with annotated running totals in two methods."""

    def __init__(self, values: list[int]) -> None:
        self.entries = list(values)

    def balance(self, opening: int = 0) -> tuple[int, int]:
        running: int = opening
        lowest: int = opening
        for entry in self.entries:
            running += entry
            lowest = min(lowest, running)
        return running, lowest

    def overdrawn(self, opening: int = 0) -> bool:
        running: int = opening
        lowest: int = opening
        for entry in self.entries:
            running += entry
            lowest = min(lowest, running)
        return lowest < 0
