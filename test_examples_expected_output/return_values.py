"""
Test cases for return value propagation.

Ensures that extracted functions with return statements
properly propagate return values in replacement calls.
"""


def early_return_a(x):
    """Early return in if statement."""
    if x < 0:
        return None
    result = x * 2
    return result


def early_return_b(x):
    """Early return in if statement (duplicate)."""
    if x < 0:
        return None
    result = x * 2
    return result


def nested_return_a(x, y):
    """Return nested in multiple if statements."""
    return __extracted_func_427(x, y)


def nested_return_b(x, y):
    """Return nested in multiple if statements (duplicate)."""
    return __extracted_func_427(x, y)


def loop_with_return_a(items):
    """Return inside loop."""
    for item in items:
        if item > 100:
            return item
    return None


def loop_with_return_b(items):
    """Return inside loop (duplicate)."""
    for item in items:
        if item > 100:
            return item
    return None


def multiple_returns_a(x):
    """Multiple return paths."""
    if x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    else:
        return "positive"


def multiple_returns_b(x):
    """Multiple return paths (duplicate)."""
    if x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    else:
        return "positive"


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


def __extracted_func_427(x, y):
    if x > 0:
        if y > 0:
            return x + y
        else:
            return x
    return 0


