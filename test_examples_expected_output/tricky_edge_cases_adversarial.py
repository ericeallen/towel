"""
Adversarial test cases: Tricky edge cases that might break extraction.

These examples test really subtle scenarios that could expose bugs:
- Multiple return statements
- Mixed data types and operator overloading
- String formatting and interpolation
- Conditional expressions (ternary operator)
- List/dict modifications vs. reassignment
"""


def conditional_return_a(x, threshold):
    """Multiple return points."""
    return __extracted_func_3428(x, threshold)


def conditional_return_b(y, limit):
    """Similar multiple return pattern."""
    return __extracted_func_3428(y, limit)


def early_return_a(items):
    """Early return for empty case."""
    return __extracted_func_3401(items)


def early_return_b(values):
    """Similar early return pattern."""
    return __extracted_func_3401(values)


def string_formatting_a(name, age):
    """String formatting operations."""
    return __extracted_func_3492(name, age)


def string_formatting_b(title, count):
    """Similar string formatting."""
    return __extracted_func_3492(title, count)


def ternary_expression_a(x, y):
    """Conditional expressions."""
    return __extracted_func_3504(x, y)


def ternary_expression_b(a, b):
    """Similar ternary pattern."""
    return __extracted_func_3504(a, b)


def list_extend_vs_assign_a(items, extra):
    """Tests list modification semantics."""
    return __extracted_func_3457(items, extra)


def list_extend_vs_assign_b(values, additional):
    """Similar list modification."""
    return __extracted_func_3457(values, additional)


def dict_update_a(base, updates):
    """Dictionary update operations."""
    return __extracted_func_3479(base, updates)


def dict_update_b(initial, changes):
    """Similar dict update pattern."""
    return __extracted_func_3479(initial, changes)


def boolean_logic_a(x, y, z):
    """Complex boolean expressions."""
    return __extracted_func_3514(x, y, z)


def boolean_logic_b(a, b, c):
    """Similar boolean logic."""
    return __extracted_func_3514(a, b, c)


def chained_comparisons_a(x, lower, upper):
    """Chained comparison operators."""
    result = False
    result = lower < x < upper
    return result


def chained_comparisons_b(y, min_val, max_val):
    """Similar chained comparison."""
    output = False
    output = min_val < y < max_val
    return output


def mixed_types_a(value):
    """Operations on mixed types."""
    return __extracted_func_3391(value)


def mixed_types_b(item):
    """Similar mixed type handling."""
    return __extracted_func_3391(item)


def slice_operations_a(items, start, end):
    """List slicing operations."""
    return __extracted_func_3523(items, start, end)


def slice_operations_b(values, begin, finish):
    """Similar slice pattern."""
    return __extracted_func_3523(values, begin, finish)


def membership_test_a(item, collection):
    """Membership testing."""
    found = False
    found = item in collection
    return found


def membership_test_b(element, group):
    """Similar membership test."""
    exists = False
    exists = element in group
    return exists


def set_operations_a(set1, set2):
    """Set union/intersection."""
    return __extracted_func_3530(set1, set2)


def set_operations_b(group1, group2):
    """Similar set operations."""
    return __extracted_func_3530(group1, group2)


def __extracted_func_3391(__param_0):
    result = 0
    if isinstance(__param_0, int):
        result = __param_0 * 2
    elif isinstance(__param_0, str):
        result = len(__param_0)
    else:
        result = -1
    return result


def __extracted_func_3401(__param_0):
    if not __param_0:
        return 0
    total = 0
    for item in __param_0:
        total += item
    return total


def __extracted_func_3428(__param_0, __param_1):
    result = __param_0 * 2
    if result > __param_1:
        return result
    result = result + 10
    return result


def __extracted_func_3457(__param_0, __param_1):
    result = []
    for item in __param_0:
        result.append(item)
    result.extend(__param_1)
    return len(result)


def __extracted_func_3479(__param_0, __param_1):
    result = {}
    for key, value in __param_0.items():
        result[key] = value
    result.update(__param_1)
    return len(result)


def __extracted_func_3492(__param_0, __param_1):
    message = ''
    message = f'Name: {__param_0}'
    message = message + f', Age: {__param_1}'
    return message


def __extracted_func_3504(__param_0, __param_1):
    result = 0
    result = __param_0 if __param_0 > __param_1 else __param_1
    result = result * 2
    return result


def __extracted_func_3514(__param_0, __param_1, __param_2):
    result = False
    result = __param_0 > 0 and __param_1 > 0
    result = result or __param_2 > 0
    return result


def __extracted_func_3523(__param_0, __param_1, __param_2):
    result = []
    result = __param_0[__param_1:__param_2]
    result = result + [999]
    return len(result)


def __extracted_func_3530(__param_0, __param_1):
    result = set()
    result = __param_0 | __param_1
    result = result & {1, 2, 3}
    return len(result)




















