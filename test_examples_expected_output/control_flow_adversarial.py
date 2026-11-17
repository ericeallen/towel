"""
Adversarial test cases: Complex control flow (break/continue/return in loops).

These examples test whether Towel correctly preserves control flow semantics
when extracting code with early exits.
"""


def find_first_even_a(numbers):
    """Returns first even number, or None."""
    result = None
    for num in numbers:
        if num % 2 == 0:
            result = num
            break
    return result


def find_first_even_b(values):
    """Similar pattern with early break."""
    return find_first_even_a(values)


def sum_until_zero_a(numbers):
    """Sum numbers until encountering zero."""
    total = 0
    for num in numbers:
        if num == 0:
            break
        total += num
    return total


def sum_until_zero_b(values):
    """Similar pattern with early break."""
    return sum_until_zero_a(values)


def skip_negatives_a(numbers):
    """Sum only non-negative numbers using continue."""
    total = 0
    for num in numbers:
        if num < 0:
            continue
        total += num
    return total


def skip_negatives_b(values):
    """Similar pattern with continue."""
    return skip_negatives_a(values)


def process_until_sentinel_a(items, sentinel):
    """Process items until sentinel is found."""
    results = []
    for item in items:
        if item == sentinel:
            return results
        results.append(item * 2)
    return results


def process_until_sentinel_b(values, stop_value):
    """Similar pattern with early return."""
    return process_until_sentinel_a(values, stop_value)


def nested_break_a(matrix):
    """Find first negative in 2D matrix."""
    found = None
    for row in matrix:
        for val in row:
            if val < 0:
                found = val
                break
        if found is not None:
            break
    return found


def nested_break_b(grid):
    """Similar nested break pattern."""
    return nested_break_a(grid)


def while_with_break_a(n):
    """While loop with break condition."""
    count = 0
    total = 0
    while count < n:
        count += 1
        total += count
        if total > 50:
            break
    return total


def while_with_break_b(limit):
    """Similar while loop with break."""
    return while_with_break_a(limit)


def continue_and_accumulate_a(items):
    """Skip some items and accumulate others."""
    result = []
    for item in items:
        if item % 3 == 0:
            continue
        if item % 2 == 0:
            result.append(item)
    return result


def continue_and_accumulate_b(values):
    """Similar skip pattern."""
    return continue_and_accumulate_a(values)
