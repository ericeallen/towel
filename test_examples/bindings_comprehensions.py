"""
Test cases for comprehension binding constructs.

Tests that comprehension variables are properly recognized as bindings.
"""


def list_comp_a(numbers):
    """List comprehension with variable 'x'."""
    result = [x * 2 for x in numbers if x > 0]
    return result


def list_comp_b(numbers):
    """List comprehension with variable 'y' (should unify with x)."""
    result = [y * 2 for y in numbers if y > 0]
    return result


def dict_comp_a(items):
    """Dict comprehension."""
    result = {k: v * 2 for k, v in items if v > 0}
    return result


def dict_comp_b(items):
    """Dict comprehension with different variable names."""
    result = {key: val * 2 for key, val in items if val > 0}
    return result


def nested_comp_a(matrix):
    """Nested comprehensions."""
    result = [[cell * 2 for cell in row] for row in matrix]
    return result


def nested_comp_b(matrix):
    """Nested comprehensions with different variable names."""
    result = [[item * 2 for item in line] for line in matrix]
    return result


def generator_expr_a(numbers):
    """Generator expression."""
    return sum(x * 2 for x in numbers if x > 0)


def generator_expr_b(numbers):
    """Generator expression with different variable name."""
    return sum(y * 2 for y in numbers if y > 0)


def set_comp_a(items):
    """Set comprehension."""
    return {item.upper() for item in items if len(item) > 2}


def set_comp_b(items):
    """Set comprehension with different variable name."""
    return {elem.upper() for elem in items if len(elem) > 2}
