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
import typing as _typing

SCALE: Final[float] = 100.0
EMPTY_LABEL: Final[str] = "empty"


def __extracted_func_2(counts: _typing.Any, key: _typing.Any, order: _typing.Any) -> None:
    if key not in counts:
        counts[key] = 0
        order.append(key)
    counts[key] += 1


def __extracted_func_0(floor: float, values: list[float]) -> tuple[int, float, float]:
    total: float = 0.0
    peak: float = float(floor)
    count: int = 0
    for value in values:
        if value < floor:
            continue
        total += value
        peak = max(peak, value)
        count += 1
    return (count, peak, total)


@dataclass(frozen=True)
class Summary:
    """The figures one pass over a series produces."""

    label: str
    total: float
    peak: float
    count: int


def summarize_values(values: list[float], *, label: str = "values", floor: float = 0) -> Summary:
    """Total, peak and count of the values at or above ``floor``."""
    count, peak, total = __extracted_func_0(floor, values)
    if count == 0:
        return Summary(EMPTY_LABEL, 0.0, 0.0, 0)
    return Summary(label, total, peak, count)


def summarize_scaled(values: list[float], *, label: str = "scaled", floor: float = 0) -> Summary:
    """The same figures after scaling every value by ``SCALE``."""
    count, peak, total = __extracted_func_0(floor, values)
    if count == 0:
        return Summary(EMPTY_LABEL, 0.0, 0.0, 0)
    return Summary(label, total * SCALE, peak * SCALE, count)


def describe_counts(items: list[object], *, width: int = 8) -> list[str]:
    """A fixed-width line per distinct item, most frequent first."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        key: str = str(item)
        __extracted_func_2(counts, key, order)
    order.sort(key=lambda key: (-counts[key], key))
    return [f"{key:<{width}}{counts[key]}" for key in order]


def describe_shares(items: list[object], *, width: int = 8) -> list[str]:
    """A fixed-width line per distinct item with its share of the whole."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        key: str = str(item)
        __extracted_func_2(counts, key, order)
    order.sort(key=lambda key: (-counts[key], key))
    total = sum(counts.values())
    return [f"{key:<{width}}{counts[key] / total:.2f}" for key in order]


class Ledger:
    """Entries with annotated running totals in two methods."""

    def __init__(self, values: list[int]) -> None:
        self.entries = list(values)

    def balance(self, opening: int = 0) -> tuple[int, int]:
        lowest, running = self.__extracted_func_1(opening)
        return running, lowest

    def overdrawn(self, opening: int = 0) -> bool:
        lowest, running = self.__extracted_func_1(opening)
        return lowest < 0

    def __extracted_func_1(self, opening: int) -> tuple[int, int]:
        running: int = opening
        lowest: int = opening
        for entry in self.entries:
            running += entry
            lowest = min(lowest, running)
        return (lowest, running)
