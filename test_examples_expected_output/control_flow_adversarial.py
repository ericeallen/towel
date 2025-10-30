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
    output = None
    for val in values:
        if val % 2 == 0:
            output = val
            break
    return output


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
    result = 0
    for val in values:
        if val == 0:
            break
        result += val
    return result


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
    result = 0
    for val in values:
        if val < 0:
            continue
        result += val
    return result


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
    output = []
    for value in values:
        if value == stop_value:
            return output
        output.append(value * 2)
    return output


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
    result = None
    for line in grid:
        for item in line:
            if item < 0:
                result = item
                break
        if result is not None:
            break
    return result


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
    i = 0
    sum_val = 0
    while i < limit:
        i += 1
        sum_val += i
        if sum_val > 50:
            break
    return sum_val


def continue_and_accumulate_a(items):
    """Skip some items and accumulate others."""
    result = []
    return __extracted_func_26(items, result)


def continue_and_accumulate_b(values):
    """Similar skip pattern."""
    output = []
    return __extracted_func_26(values, output)


def __extracted_func_26(__param_17, __param_18):
    for item in __param_17:
        if item % 3 == 0:
            continue
        if item % 2 == 0:
            __param_18.append(item)
    return __param_18


