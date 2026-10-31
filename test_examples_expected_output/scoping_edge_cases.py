"""
Test cases for scoping edge cases.

Tests:
1. Nested function definitions
2. Lambda expressions
3. Closures and free variables
4. Name shadowing
"""


def __extracted_func_2(items, _towel_owner):
    result = []
    for item in items:
        if len(item) > 0:
            result.append(str(item))
    return result


def __extracted_func_1(items, multiplier, _towel_owner):
    result = []
    for item in items:
        if item > 0:
            result.append(item * multiplier)
    return result


def __extracted_func_0(items, _towel_owner):
    processor = lambda x: x * 2
    result = []
    for item in items:
        if item > 0:
            result.append(processor(item))
    return result


def with_nested_func_a(items):
    """Nested function definition."""

    def helper(x):
        return x * 2

    result = []
    for item in items:
        if item > 0:
            result.append(helper(item))
    return result


def with_nested_func_b(items):
    """Nested function definition (duplicate outer, different inner name)."""

    def processor(x):
        return x * 2

    result = []
    for item in items:
        if item > 0:
            result.append(processor(item))
    return result


def with_lambda_a(items):
    """Lambda expression."""
    _towel_arguments = [(items,)]
    del items
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def with_lambda_b(items):
    """Lambda expression (duplicate)."""
    _towel_arguments = [(items,)]
    del items
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def closure_a(multiplier):
    """Function that creates closure."""

    def process(items):
        _towel_arguments_1 = [(items,)]
        del items
        return __extracted_func_1(_towel_arguments_1[0][0], multiplier, _towel_arguments_1.pop())

    return process


def closure_b(multiplier):
    """Function that creates closure (duplicate)."""

    def process(items):
        _towel_arguments_1 = [(items,)]
        del items
        return __extracted_func_1(_towel_arguments_1[0][0], multiplier, _towel_arguments_1.pop())

    return process


def shadowing_a(x):
    """Variable shadowing in nested scope."""
    result = x * 2
    if True:
        x = 10
        result = result + x
    return result


def shadowing_b(x):
    """Variable shadowing in nested scope (duplicate)."""
    result = x * 2
    if True:
        x = 10
        result = result + x
    return result


def builtin_override_a(items):
    """Don't treat builtin names as parameters."""
    _towel_arguments_2 = [(items,)]
    del items
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def builtin_override_b(items):
    """Don't treat builtin names as parameters (duplicate)."""
    _towel_arguments_2 = [(items,)]
    del items
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())
