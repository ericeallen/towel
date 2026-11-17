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
    result = x * 2
    if result > threshold:
        return result
    result = result + 10
    return result


def conditional_return_b(y, limit):
    """Similar multiple return pattern."""
    return conditional_return_a(y, limit)


def early_return_a(items):
    """Early return for empty case."""
    if not items:
        return 0
    total = 0
    for item in items:
        total += item
    return total


def early_return_b(values):
    """Similar early return pattern."""
    return early_return_a(values)


def string_formatting_a(name, age):
    """String formatting operations."""
    message = ""
    message = f"Name: {name}"
    message = message + f", Age: {age}"
    return message


def string_formatting_b(title, count):
    """Similar string formatting."""
    return string_formatting_a(title, count)


def ternary_expression_a(x, y):
    """Conditional expressions."""
    result = 0
    result = x if x > y else y
    result = result * 2
    return result


def ternary_expression_b(a, b):
    """Similar ternary pattern."""
    return ternary_expression_a(a, b)


def list_extend_vs_assign_a(items, extra):
    """Tests list modification semantics."""
    result = []
    for item in items:
        result.append(item)
    result.extend(extra)
    return len(result)


def list_extend_vs_assign_b(values, additional):
    """Similar list modification."""
    return list_extend_vs_assign_a(values, additional)


def dict_update_a(base, updates):
    """Dictionary update operations."""
    result = {}
    for key, value in base.items():
        result[key] = value
    result.update(updates)
    return len(result)


def dict_update_b(initial, changes):
    """Similar dict update pattern."""
    return dict_update_a(initial, changes)


def boolean_logic_a(x, y, z):
    """Complex boolean expressions."""
    result = False
    result = x > 0 and y > 0
    result = result or z > 0
    return result


def boolean_logic_b(a, b, c):
    """Similar boolean logic."""
    return boolean_logic_a(a, b, c)


def chained_comparisons_a(x, lower, upper):
    """Chained comparison operators."""
    result = False
    result = lower < x < upper
    return result


def chained_comparisons_b(y, min_val, max_val):
    """Similar chained comparison."""
    return chained_comparisons_a(y, min_val, max_val)


def mixed_types_a(value):
    """Operations on mixed types."""
    result = 0
    if isinstance(value, int):
        result = value * 2
    elif isinstance(value, str):
        result = len(value)
    else:
        result = -1
    return result


def mixed_types_b(item):
    """Similar mixed type handling."""
    return mixed_types_a(item)


def slice_operations_a(items, start, end):
    """List slicing operations."""
    result = []
    result = items[start:end]
    result = result + [999]
    return len(result)


def slice_operations_b(values, begin, finish):
    """Similar slice pattern."""
    return slice_operations_a(values, begin, finish)


def membership_test_a(item, collection):
    """Membership testing."""
    found = False
    found = item in collection
    return found


def membership_test_b(element, group):
    """Similar membership test."""
    return membership_test_a(element, group)


def set_operations_a(set1, set2):
    """Set union/intersection."""
    result = set()
    result = set1 | set2
    result = result & {1, 2, 3}
    return len(result)


def set_operations_b(group1, group2):
    """Similar set operations."""
    return set_operations_a(group1, group2)
