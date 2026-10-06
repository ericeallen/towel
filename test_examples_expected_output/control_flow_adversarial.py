"""
Adversarial test cases: Complex control flow (break/continue/return in loops).

These examples test whether Towel correctly preserves control flow semantics
when extracting code with early exits.
"""


def __extracted_func_6(__param_0, __param_1, _towel_owner):
    results = []
    for item in __param_0:
        if item == __param_1:
            return results
        results.append(item * 2)
    return results


def __extracted_func_5(__param_0, _towel_owner):
    total = 0
    for num in __param_0:
        if num < 0:
            continue
        total += num
    return total


def __extracted_func_4(__param_0, _towel_owner):
    total = 0
    for num in __param_0:
        if num == 0:
            break
        total += num
    return total


def __extracted_func_3(__param_0, _towel_owner):
    result = None
    for num in __param_0:
        if num % 2 == 0:
            result = num
            break
    return result


def __extracted_func_2(__param_0, _towel_owner):
    result = []
    for item in __param_0:
        if item % 3 == 0:
            continue
        if item % 2 == 0:
            result.append(item)
    return result


def __extracted_func_1(__param_0, _towel_owner):
    count = 0
    total = 0
    while count < __param_0:
        count += 1
        total += count
        if total > 50:
            break
    return total


def __extracted_func_0(__param_0, _towel_owner):
    found = None
    for row in __param_0:
        for val in row:
            if val < 0:
                found = val
                break
        if found is not None:
            break
    return found


def find_first_even_a(numbers):
    """Returns first even number, or None."""
    _towel_arguments_3 = [(numbers,)]
    del numbers
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def find_first_even_b(values):
    """Similar pattern with early break."""
    _towel_arguments_3 = [(values,)]
    del values
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def sum_until_zero_a(numbers):
    """Sum numbers until encountering zero."""
    _towel_arguments_4 = [(numbers,)]
    del numbers
    return __extracted_func_4(_towel_arguments_4[0][0], _towel_arguments_4.pop())


def sum_until_zero_b(values):
    """Similar pattern with early break."""
    _towel_arguments_4 = [(values,)]
    del values
    return __extracted_func_4(_towel_arguments_4[0][0], _towel_arguments_4.pop())


def skip_negatives_a(numbers):
    """Sum only non-negative numbers using continue."""
    _towel_arguments_5 = [(numbers,)]
    del numbers
    return __extracted_func_5(_towel_arguments_5[0][0], _towel_arguments_5.pop())


def skip_negatives_b(values):
    """Similar pattern with continue."""
    _towel_arguments_5 = [(values,)]
    del values
    return __extracted_func_5(_towel_arguments_5[0][0], _towel_arguments_5.pop())


def process_until_sentinel_a(items, sentinel):
    """Process items until sentinel is found."""
    _towel_arguments_6 = [(sentinel, items)]
    del items
    del sentinel
    return __extracted_func_6(_towel_arguments_6[0][1], _towel_arguments_6[0][0], _towel_arguments_6.pop())


def process_until_sentinel_b(values, stop_value):
    """Similar pattern with early return."""
    _towel_arguments_6 = [(stop_value, values)]
    del values
    del stop_value
    return __extracted_func_6(_towel_arguments_6[0][1], _towel_arguments_6[0][0], _towel_arguments_6.pop())


def nested_break_a(matrix):
    """Find first negative in 2D matrix."""
    _towel_arguments = [(matrix,)]
    del matrix
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def nested_break_b(grid):
    """Similar nested break pattern."""
    _towel_arguments = [(grid,)]
    del grid
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def while_with_break_a(n):
    """While loop with break condition."""
    _towel_arguments_1 = [(n,)]
    del n
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def while_with_break_b(limit):
    """Similar while loop with break."""
    _towel_arguments_1 = [(limit,)]
    del limit
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def continue_and_accumulate_a(items):
    """Skip some items and accumulate others."""
    _towel_arguments_2 = [(items,)]
    del items
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def continue_and_accumulate_b(values):
    """Similar skip pattern."""
    _towel_arguments_2 = [(values,)]
    del values
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())
