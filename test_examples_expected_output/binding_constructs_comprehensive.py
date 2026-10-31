"""
Comprehensive test cases for all Python binding constructs.

Tests that Towel correctly handles free variable analysis for:
- Walrus operator (:=)
- With statements (with ... as)
- Async constructs (async for, async with)
"""


def __extracted_func_5(file1, file2, _towel_owner):
    data = []
    with open(file1) as f1, open(file2) as f2:
        data.append(f1.read())
        data.append(f2.read())
    return data


def __extracted_func_4(filename, _towel_owner):
    lines = []
    with open(filename) as f:
        for line in f:
            lines.append(line.strip())
    return lines


def __extracted_func_3(items, _towel_owner):
    result = []
    if (n := len(items)) > 0:
        result.append(n)
        result.append(n * 2)
    return result


def __extracted_func_2(items, _towel_owner):
    results = []
    idx = 0
    while (item := (items[idx] if idx < len(items) else None)) is not None:
        results.append(item * 2)
        idx += 1
    return results


def __extracted_func_1(inner_file, outer_file, _towel_owner):
    result = []
    with open(outer_file) as f1:
        result.append(f1.readline())
        with open(inner_file) as f2:
            result.append(f2.readline())
    return result


def __extracted_func_0(data, _towel_owner):
    errors = []
    for item in data:
        try:
            result = int(item)
        except ValueError as e:
            errors.append(str(e))
    return errors


def test_walrus_basic_a(items):
    """Walrus operator in if condition."""
    _towel_arguments_3 = [(items,)]
    del items
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def test_walrus_basic_b(items):
    """Duplicate with walrus operator."""
    _towel_arguments_3 = [(items,)]
    del items
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def test_with_statement_a(filename):
    """With statement binding."""
    _towel_arguments_4 = [(filename,)]
    del filename
    return __extracted_func_4(_towel_arguments_4[0][0], _towel_arguments_4.pop())


def test_with_statement_b(filename):
    """Duplicate with statement."""
    _towel_arguments_4 = [(filename,)]
    del filename
    return __extracted_func_4(_towel_arguments_4[0][0], _towel_arguments_4.pop())


def test_with_multiple_a(file1, file2):
    """Multiple context managers."""
    _towel_arguments_5 = [(file2, file1)]
    del file1
    del file2
    return __extracted_func_5(_towel_arguments_5[0][1], _towel_arguments_5[0][0], _towel_arguments_5.pop())


def test_with_multiple_b(file1, file2):
    """Duplicate multiple context managers."""
    _towel_arguments_5 = [(file2, file1)]
    del file1
    del file2
    return __extracted_func_5(_towel_arguments_5[0][1], _towel_arguments_5[0][0], _towel_arguments_5.pop())


def test_walrus_in_comprehension_a(items):
    """Walrus in list comprehension."""
    # Note: Walrus leaks from comprehension scope
    results = [(y := x * 2) for x in items if x > 0]
    return results


def test_walrus_in_comprehension_b(items):
    """Duplicate walrus in comprehension."""
    results = [(y := x * 2) for x in items if x > 0]
    return results


def test_nested_with_a(outer_file, inner_file):
    """Nested with statements."""
    _towel_arguments_1 = [(inner_file, outer_file)]
    del outer_file
    del inner_file
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1[0][1], _towel_arguments_1.pop())


def test_nested_with_b(outer_file, inner_file):
    """Duplicate nested with statements."""
    _towel_arguments_1 = [(inner_file, outer_file)]
    del outer_file
    del inner_file
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1[0][1], _towel_arguments_1.pop())


def test_exception_handler_binding_a(data):
    """Exception variable binding."""
    _towel_arguments = [(data,)]
    del data
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def test_exception_handler_binding_b(data):
    """Duplicate exception variable binding."""
    _towel_arguments = [(data,)]
    del data
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def test_walrus_while_a(items):
    """Walrus in while loop condition."""
    _towel_arguments_2 = [(items,)]
    del items
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def test_walrus_while_b(items):
    """Duplicate walrus in while loop."""
    _towel_arguments_2 = [(items,)]
    del items
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())
