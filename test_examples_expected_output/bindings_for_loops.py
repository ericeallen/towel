"""
Test cases for for-loop binding constructs.

Tests alpha-renaming: different loop variable names (i vs j) should unify.
"""


def process_list_a(items):
    """Process list with loop variable 'i'."""
    result = []
    for i in range(len(items)):
        if items[i] > 0:
            result.append(items[i] * 2)
    return result


def process_list_b(items):
    """Process list with loop variable 'j' (should unify with i)."""
    result = []
    for j in range(len(items)):
        if items[j] > 0:
            result.append(items[j] * 2)
    return result


def nested_loops_a(matrix):
    """Nested loops with i, j."""
    return __extracted_func_24(matrix)


def nested_loops_b(matrix):
    """Nested loops with x, y (should unify with i, j)."""
    return __extracted_func_24(matrix)


def tuple_unpacking_a(pairs):
    """For loop with tuple unpacking."""
    result = {}
    for key, value in pairs:
        if value is not None:
            result[key] = value * 2
    return result


def tuple_unpacking_b(pairs):
    """For loop with tuple unpacking (different var names)."""
    result = {}
    for k, v in pairs:
        if v is not None:
            result[k] = v * 2
    return result


def __extracted_func_24(matrix):
    total = 0
    for i in range(len(matrix)):
        for j in range(len(matrix[i])):
            if matrix[i][j] > 0:
                total += matrix[i][j]
    return total


