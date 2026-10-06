"""
Test cases for for-loop binding constructs.

Tests alpha-renaming: different loop variable names (i vs j) should unify.
"""


def __extracted_func_2(pairs, _towel_owner):
    result = {}
    for key, value in pairs:
        if value is not None:
            result[key] = value * 2
    return result


def __extracted_func_1(items, _towel_owner):
    result = []
    for i in range(len(items)):
        if items[i] > 0:
            result.append(items[i] * 2)
    return result


def __extracted_func_0(matrix, _towel_owner):
    total = 0
    for i in range(len(matrix)):
        for j in range(len(matrix[i])):
            if matrix[i][j] > 0:
                total += matrix[i][j]
    return total


def process_list_a(items):
    """Process list with loop variable 'i'."""
    _towel_arguments_1 = [(items,)]
    del items
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def process_list_b(items):
    """Process list with loop variable 'j' (should unify with i)."""
    _towel_arguments_1 = [(items,)]
    del items
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def nested_loops_a(matrix):
    """Nested loops with i, j."""
    _towel_arguments = [(matrix,)]
    del matrix
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def nested_loops_b(matrix):
    """Nested loops with x, y (should unify with i, j)."""
    _towel_arguments = [(matrix,)]
    del matrix
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def tuple_unpacking_a(pairs):
    """For loop with tuple unpacking."""
    _towel_arguments_2 = [(pairs,)]
    del pairs
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def tuple_unpacking_b(pairs):
    """For loop with tuple unpacking (different var names)."""
    _towel_arguments_2 = [(pairs,)]
    del pairs
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())
