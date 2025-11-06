"""
Adversarial test cases: Complex control flow (break/continue/return in loops).

These examples test whether Towel correctly preserves control flow semantics
when extracting code with early exits.
"""


def find_first_even_a(numbers):
    """Returns first even number, or None."""
    return __extracted_func_3(numbers)


def find_first_even_b(values):
    """Similar pattern with early break."""
    return __extracted_func_3(values)


def sum_until_zero_a(numbers):
    """Sum numbers until encountering zero."""
    return __extracted_func_4(numbers)


def sum_until_zero_b(values):
    """Similar pattern with early break."""
    return __extracted_func_4(values)


def skip_negatives_a(numbers):
    """Sum only non-negative numbers using continue."""
    return __extracted_func_5(numbers)


def skip_negatives_b(values):
    """Similar pattern with continue."""
    return __extracted_func_5(values)


def process_until_sentinel_a(items, sentinel):
    """Process items until sentinel is found."""
    return __extracted_func_6(items, sentinel)


def process_until_sentinel_b(values, stop_value):
    """Similar pattern with early return."""
    return __extracted_func_6(values, stop_value)


def nested_break_a(matrix):
    """Find first negative in 2D matrix."""
    return extracted_func(matrix)


def nested_break_b(grid):
    """Similar nested break pattern."""
    return extracted_func(grid)


def while_with_break_a(n):
    """While loop with break condition."""
    return __extracted_func_1(n)


def while_with_break_b(limit):
    """Similar while loop with break."""
    return __extracted_func_1(limit)


def continue_and_accumulate_a(items):
    """Skip some items and accumulate others."""
    return __extracted_func_2(items)


def continue_and_accumulate_b(values):
    """Similar skip pattern."""
    return __extracted_func_2(values)


def extracted_func(__param_0):
    found = None
    for row in __param_0:
        for val in row:
            if val < 0:
                found = val
                break
        if found is not None:
            break
    return found


def __extracted_func_1(__param_0):
    count = 0
    total = 0
    while count < __param_0:
        count += 1
        total += count
        if total > 50:
            break
    return total


def __extracted_func_2(__param_0):
    result = []
    for item in __param_0:
        if item % 3 == 0:
            continue
        if item % 2 == 0:
            result.append(item)
    return result


def __extracted_func_3(__param_0):
    result = None
    for num in __param_0:
        if num % 2 == 0:
            result = num
            break
    return result


def __extracted_func_4(__param_0):
    total = 0
    for num in __param_0:
        if num == 0:
            break
        total += num
    return total


def __extracted_func_5(__param_0):
    total = 0
    for num in __param_0:
        if num < 0:
            continue
        total += num
    return total


def __extracted_func_6(__param_0, __param_1):
    results = []
    for item in __param_0:
        if item == __param_1:
            return results
        results.append(item * 2)
    return results














