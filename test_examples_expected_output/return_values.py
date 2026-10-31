"""
Test cases for return value propagation.

Ensures that extracted functions with return statements
properly propagate return values in replacement calls.
"""


def __extracted_func_3(items, _towel_owner):
    for item in items:
        if item > 100:
            return item
    return None


def __extracted_func_2(x, _towel_owner):
    if x < 0:
        return None
    result = x * 2
    return result


def __extracted_func_1(x):
    if x < 0:
        return 'negative'
    elif x == 0:
        return 'zero'
    else:
        return 'positive'


def __extracted_func_0(x, y):
    if x > 0:
        if y > 0:
            return x + y
        else:
            return x
    return 0


def early_return_a(x):
    """Early return in if statement."""
    _towel_arguments = [(x,)]
    del x
    return __extracted_func_2(_towel_arguments[0][0], _towel_arguments.pop())


def early_return_b(x):
    """Early return in if statement (duplicate)."""
    _towel_arguments = [(x,)]
    del x
    return __extracted_func_2(_towel_arguments[0][0], _towel_arguments.pop())


def nested_return_a(x, y):
    """Return nested in multiple if statements."""
    return __extracted_func_0(x, y)


def nested_return_b(x, y):
    """Return nested in multiple if statements (duplicate)."""
    return __extracted_func_0(x, y)


def loop_with_return_a(items):
    """Return inside loop."""
    _towel_arguments_1 = [(items,)]
    del items
    return __extracted_func_3(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def loop_with_return_b(items):
    """Return inside loop (duplicate)."""
    _towel_arguments_1 = [(items,)]
    del items
    return __extracted_func_3(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def multiple_returns_a(x):
    """Multiple return paths."""
    return __extracted_func_1(x)


def multiple_returns_b(x):
    """Multiple return paths (duplicate)."""
    return __extracted_func_1(x)


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
