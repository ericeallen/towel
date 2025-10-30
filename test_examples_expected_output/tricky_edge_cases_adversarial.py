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
    return __extracted_func_419(result, threshold)


def conditional_return_b(y, limit):
    """Similar multiple return pattern."""
    output = y * 2
    return __extracted_func_419(output, limit)


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
    if not values:
        return 0
    sum_val = 0
    for value in values:
        sum_val += value
    return sum_val


def string_formatting_a(name, age):
    """String formatting operations."""
    message = ""
    message = f"Name: {name}"
    message = message + f", Age: {age}"
    return message


def string_formatting_b(title, count):
    """Similar string formatting."""
    text = ""
    text = f"Name: {title}"
    text = text + f", Age: {count}"
    return text


def ternary_expression_a(x, y):
    """Conditional expressions."""
    result = 0
    result = x if x > y else y
    result = result * 2
    return result


def ternary_expression_b(a, b):
    """Similar ternary pattern."""
    output = 0
    output = a if a > b else b
    output = output * 2
    return output


def list_extend_vs_assign_a(items, extra):
    """Tests list modification semantics."""
    result = []
    for item in items:
        result.append(item)
    result.extend(extra)
    return len(result)


def list_extend_vs_assign_b(values, additional):
    """Similar list modification."""
    output = []
    for value in values:
        output.append(value)
    output.extend(additional)
    return len(output)


def dict_update_a(base, updates):
    """Dictionary update operations."""
    result = {}
    for key, value in base.items():
        result[key] = value
    result.update(updates)
    return len(result)


def dict_update_b(initial, changes):
    """Similar dict update pattern."""
    output = {}
    for k, v in initial.items():
        output[k] = v
    output.update(changes)
    return len(output)


def boolean_logic_a(x, y, z):
    """Complex boolean expressions."""
    result = False
    result = x > 0 and y > 0
    result = result or z > 0
    return result


def boolean_logic_b(a, b, c):
    """Similar boolean logic."""
    output = False
    output = a > 0 and b > 0
    output = output or c > 0
    return output


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
    output = 0
    if isinstance(item, int):
        output = item * 2
    elif isinstance(item, str):
        output = len(item)
    else:
        output = -1
    return output


def slice_operations_a(items, start, end):
    """List slicing operations."""
    result = []
    result = items[start:end]
    result = result + [999]
    return len(result)


def slice_operations_b(values, begin, finish):
    """Similar slice pattern."""
    output = []
    output = values[begin:finish]
    output = output + [999]
    return len(output)


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
    result = set()
    result = set1 | set2
    result = result & {1, 2, 3}
    return len(result)


def set_operations_b(group1, group2):
    """Similar set operations."""
    output = set()
    output = group1 | group2
    output = output & {1, 2, 3}
    return len(output)


def __extracted_func_419(__param_704, __param_705):
    if __param_704 > __param_705:
        return __param_704
    result = __param_704 + 10
    return __param_704


