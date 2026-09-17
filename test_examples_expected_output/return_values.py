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
    return early_return_a(x)


def nested_return_a(x, y):
    """Return nested in multiple if statements."""
    if x > 0:
        if y > 0:
            return x + y
        else:
            return x
    return 0


def nested_return_b(x, y):
    """Return nested in multiple if statements (duplicate)."""
    return nested_return_a(x, y)


def loop_with_return_a(items):
    """Return inside loop."""
    for item in items:
        if item > 100:
            return item
    return None


def loop_with_return_b(items):
    """Return inside loop (duplicate)."""
    return loop_with_return_a(items)


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
    return multiple_returns_a(x)


def no_return_a(x):
    """Function with no explicit return (returns None)."""
    result = []
    for i in range(x):
        result.append(i * 2)
    # No return statement


def no_return_b(x):
    """Function with no explicit return (duplicate)."""
    no_return_a(x)
    # No return statement
