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

    result = []
    for item in items:
        if item > 0:
            result.append(processor(item))
    return result


def with_lambda_a(items):
    """Lambda expression."""
    processor = lambda x: x * 2
    result = []
    for item in items:
        if item > 0:
            result.append(processor(item))
    return result


def with_lambda_b(items):
    """Lambda expression (duplicate)."""
    processor = lambda y: y * 2
    result = []
    for item in items:
        if item > 0:
            result.append(processor(item))
    return result


def closure_a(multiplier):
    """Function that creates closure."""
    return __extracted_func_450(multiplier)


def closure_b(multiplier):
    """Function that creates closure (duplicate)."""
    return __extracted_func_450(multiplier)


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
    result = []
    for item in items:
        if len(item) > 0:
            result.append(str(item))
    return result


def builtin_override_b(items):
    """Don't treat builtin names as parameters (duplicate)."""
    result = []
    for item in items:
        if len(item) > 0:
            result.append(str(item))
    return result


def __extracted_func_450(multiplier):

    def process(items):
        result = []
        for item in items:
            if item > 0:
                result.append(item * multiplier)
        return result
    return process


