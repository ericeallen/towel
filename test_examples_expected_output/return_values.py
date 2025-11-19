"""
Test cases for return value propagation.

Ensures that extracted functions with return statements
properly propagate return values in replacement calls.
"""


def early_return_a(x):
    """Early return in if statement."""
    return extracted_func(x)


def early_return_b(x):
    """Early return in if statement (duplicate)."""
    return extracted_func(x)


def nested_return_a(x, y):
    """Return nested in multiple if statements."""
    return extracted_func(x, y)


def nested_return_b(x, y):
    """Return nested in multiple if statements (duplicate)."""
    return extracted_func(x, y)


def loop_with_return_a(items):
    """Return inside loop."""
    return extracted_func(items)


def loop_with_return_b(items):
    """Return inside loop (duplicate)."""
    return extracted_func(items)


def multiple_returns_a(x):
    """Multiple return paths."""
    return extracted_func(x)


def multiple_returns_b(x):
    """Multiple return paths (duplicate)."""
    return extracted_func(x)


def no_return_a(x):
    """Function with no explicit return (returns None)."""
    extracted_func(x)
    # No return statement


def no_return_b(x):
    """Function with no explicit return (duplicate)."""
    extracted_func(x)
    # No return statement


def extracted_func(x, y):
    if x > 0:
        if y > 0:
            return x + y
        else:
            return x
    return 0


def extracted_func(x):
    if x < 0:
        return 'negative'
    elif x == 0:
        return 'zero'
    else:
        return 'positive'


def extracted_func(x):
    if x < 0:
        return None
    result = x * 2
    return result


def extracted_func(items):
    for item in items:
        if item > 100:
            return item
    return None


def extracted_func(x):
    result = []
    for i in range(x):
        result.append(i * 2)










