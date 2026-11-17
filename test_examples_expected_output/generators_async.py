"""
Generators and coroutines: duplicated blocks inside generator functions and
async functions.

A block that contains ``yield`` or ``await`` suspends the frame it runs in
and must stay where it is; the duplicated blocks before and after such a
statement can still move into a helper.
"""

import asyncio


async def __extracted_func_2(items):
    seen = 0
    for item in items:
        await asyncio.sleep(0)
        seen += 1
        if item is None:
            break
    return seen


def __extracted_func_1(values, size):
    if size <= 0:
        raise ValueError("size must be positive")
    cleaned = [value for value in values if value is not None]
    limit = len(cleaned) - len(cleaned) % size
    window = cleaned[:limit]
    return (limit, window)


def __extracted_func_0(values, size, _param_0):
    limit, window = __extracted_func_1(values, size)
    total = _param_0
    count = 0
    return (window, total, count)


def batched_totals(values, size):
    """Sum each full run of ``size`` values, one total per run."""
    window, total, count = __extracted_func_0(values, size, 0)
    for value in window:
        total += value
        count += 1
        if count == size:
            yield total
            total = 0
            count = 0


def batched_peaks(values, size):
    """The largest value of each full run of ``size`` values."""
    window, peak, count = __extracted_func_0(values, size, None)
    for value in window:
        peak = value if peak is None or value > peak else peak
        count += 1
        if count == size:
            yield peak
            peak = None
            count = 0


def numbered_lines(lines):
    """Each line with its one-based number, then a terminator."""
    number = 0
    for line in lines:
        number += 1
        yield number, line.rstrip()
    yield number, "<end>"


def numbered_words(text):
    """Each word with its one-based number, then a terminator."""
    number = 0
    for line in text.split():
        number += 1
        yield number, line.rstrip()
    yield number, "<end>"


async def settle_totals(values, size):
    """Batch totals after yielding to the event loop once."""
    limit, window = __extracted_func_1(values, size)
    await asyncio.sleep(0)
    return [sum(window[start : start + size]) for start in range(0, limit, size)]


async def settle_peaks(values, size):
    """Batch peaks after yielding to the event loop once."""
    limit, window = __extracted_func_1(values, size)
    await asyncio.sleep(0)
    return [max(window[start : start + size]) for start in range(0, limit, size)]


async def drain_counts(items):
    """Count items, pausing after each so other tasks can run."""
    seen = await __extracted_func_2(items)
    return seen


async def drain_lengths(items):
    """Twice the count of items drained, pausing after each."""
    seen = await __extracted_func_2(items)
    return seen * 2
