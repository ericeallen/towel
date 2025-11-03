"""
Test cases for return value propagation.

Ensures that extracted functions with return statements
properly propagate return values in replacement calls.
"""


def early_return_a(x):
    """Early return in if statement."""
    return __extracted_func_2118(x)


def early_return_b(x):
    """Early return in if statement (duplicate)."""
    return __extracted_func_2118(x)


def nested_return_a(x, y):
    """Return nested in multiple if statements."""
    return __extracted_func_2106(x, y)


def nested_return_b(x, y):
    """Return nested in multiple if statements (duplicate)."""
    return __extracted_func_2106(x, y)


def loop_with_return_a(items):
    """Return inside loop."""
    return __extracted_func_2122(items)


def loop_with_return_b(items):
    """Return inside loop (duplicate)."""
    return __extracted_func_2122(items)


def multiple_returns_a(x):
    """Multiple return paths."""
    return __extracted_func_2115(x)


def multiple_returns_b(x):
    """Multiple return paths (duplicate)."""
    return __extracted_func_2115(x)


def no_return_a(x):
    """Function with no explicit return (returns None)."""
    __extracted_func_2124(x)
    # No return statement


def no_return_b(x):
    """Function with no explicit return (duplicate)."""
    __extracted_func_2124(x)
    # No return statement


def __extracted_func_2106(x, y):
    if x > 0:
        if y > 0:
            return x + y
        else:
            return x
    return 0


def __extracted_func_2115(x):
    if x < 0:
        return 'negative'
    elif x == 0:
        return 'zero'
    else:
        return 'positive'


def __extracted_func_2118(x):
    if x < 0:
        return None
    result = x * 2
    return result


def __extracted_func_2122(items):
    for item in items:
        if item > 100:
            return item
    return None


def __extracted_func_2124(x):
    result = []
    for i in range(x):
        result.append(i * 2)










