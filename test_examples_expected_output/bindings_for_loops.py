"""
Test cases for for-loop binding constructs.

Tests alpha-renaming: different loop variable names (i vs j) should unify.
"""


def process_list_a(items):
    """Process list with loop variable 'i'."""
    return extracted_func(items)


def process_list_b(items):
    """Process list with loop variable 'j' (should unify with i)."""
    return extracted_func(items)


def nested_loops_a(matrix):
    """Nested loops with i, j."""
    return extracted_func(matrix)


def nested_loops_b(matrix):
    """Nested loops with x, y (should unify with i, j)."""
    return extracted_func(matrix)


def tuple_unpacking_a(pairs):
    """For loop with tuple unpacking."""
    return extracted_func(pairs)


def tuple_unpacking_b(pairs):
    """For loop with tuple unpacking (different var names)."""
    return extracted_func(pairs)


def extracted_func(matrix):
    total = 0
    for i in range(len(matrix)):
        for j in range(len(matrix[i])):
            if matrix[i][j] > 0:
                total += matrix[i][j]
    return total


def extracted_func(items):
    result = []
    for i in range(len(items)):
        if items[i] > 0:
            result.append(items[i] * 2)
    return result


def extracted_func(pairs):
    result = {}
    for key, value in pairs:
        if value is not None:
            result[key] = value * 2
    return result






