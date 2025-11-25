"""
Test cases for scoping edge cases.

Tests:
1. Nested function definitions
2. Lambda expressions
3. Closures and free variables
4. Name shadowing
"""


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

    return __extracted_func_0(items, processor)


def with_lambda_a(items):
    """Lambda expression."""
    processor = lambda x: x * 2
    return __extracted_func_0(items, processor)


def with_lambda_b(items):
    """Lambda expression (duplicate)."""
    processor = lambda y: y * 2
    return __extracted_func_0(items, processor)


def closure_a(multiplier):
    """Function that creates closure."""

    def process(items):
        return __extracted_func_1(items, multiplier)

    return process


def closure_b(multiplier):
    """Function that creates closure (duplicate)."""

    def process(items):
        return __extracted_func_1(items, multiplier)

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
    return __extracted_func_2(items)


def builtin_override_b(items):
    """Don't treat builtin names as parameters (duplicate)."""
    return __extracted_func_2(items)


def __extracted_func_0(items, processor):
    result = []
    for item in items:
        if item > 0:
            result.append(processor(item))
    return result


def __extracted_func_1(items, multiplier):
    result = []
    for item in items:
        if item > 0:
            result.append(item * multiplier)
    return result


def __extracted_func_2(items):
    result = []
    for item in items:
        if len(item) > 0:
            result.append(str(item))
    return result






