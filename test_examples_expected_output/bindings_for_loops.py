"""
Test cases for for-loop binding constructs.

Tests alpha-renaming: different loop variable names (i vs j) should unify.
"""


def process_list_a(items):
    """Process list with loop variable 'i'."""
    return __extracted_func_97(items)


def process_list_b(items):
    """Process list with loop variable 'j' (should unify with i)."""
    return __extracted_func_97(items)


def nested_loops_a(matrix):
    """Nested loops with i, j."""
    return __extracted_func_91(matrix)


def nested_loops_b(matrix):
    """Nested loops with x, y (should unify with i, j)."""
    return __extracted_func_91(matrix)


def tuple_unpacking_a(pairs):
    """For loop with tuple unpacking."""
    return __extracted_func_105(pairs)


def tuple_unpacking_b(pairs):
    """For loop with tuple unpacking (different var names)."""
    return __extracted_func_105(pairs)


def __extracted_func_91(matrix):
    total = 0
    for i in range(len(matrix)):
        for j in range(len(matrix[i])):
            if matrix[i][j] > 0:
                total += matrix[i][j]
    return total


def __extracted_func_97(items):
    result = []
    for i in range(len(items)):
        if items[i] > 0:
            result.append(items[i] * 2)
    return result


def __extracted_func_105(pairs):
    result = {}
    for key, value in pairs:
        if value is not None:
            result[key] = value * 2
    return result






