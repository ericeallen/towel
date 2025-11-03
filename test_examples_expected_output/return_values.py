"""
Test cases for return value propagation.

Ensures that extracted functions with return statements
properly propagate return values in replacement calls.
"""


def early_return_a(x):
    """Early return in if statement."""
    return __extracted_func_1599(x)


def early_return_b(x):
    """Early return in if statement (duplicate)."""
    return __extracted_func_1599(x)


def nested_return_a(x, y):
    """Return nested in multiple if statements."""
    return __extracted_func_1590(x, y)


def nested_return_b(x, y):
    """Return nested in multiple if statements (duplicate)."""
    return __extracted_func_1590(x, y)


def loop_with_return_a(items):
    """Return inside loop."""
    return __extracted_func_1601(items)


def loop_with_return_b(items):
    """Return inside loop (duplicate)."""
    return __extracted_func_1601(items)


def multiple_returns_a(x):
    """Multiple return paths."""
    return __extracted_func_1597(x)


def multiple_returns_b(x):
    """Multiple return paths (duplicate)."""
    return __extracted_func_1597(x)


def no_return_a(x):
    """Function with no explicit return (returns None)."""
    result = []
    for i in range(x):
        result.append(i * 2)
    # No return statement


def no_return_b(x):
    """Function with no explicit return (duplicate)."""
    result = []
    for i in range(x):
        result.append(i * 2)
    # No return statement


def __extracted_func_1590(x, y):
    if x > 0:
        if y > 0:
            return x + y
        else:
            return x
    return 0


def __extracted_func_1597(x):
    if x < 0:
        return 'negative'
    elif x == 0:
        return 'zero'
    else:
        return 'positive'


def __extracted_func_1599(x):
    if x < 0:
        return None
    result = x * 2
    return result


def __extracted_func_1601(items):
    for item in items:
        if item > 100:
            return item
    return None








