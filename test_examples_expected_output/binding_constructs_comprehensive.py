"""
Comprehensive test cases for all Python binding constructs.

Tests that Towel correctly handles free variable analysis for:
- Walrus operator (:=)
- With statements (with ... as)
- Async constructs (async for, async with)
"""


def test_walrus_basic_a(items):
    """Walrus operator in if condition."""
    return __extracted_func_63(items)


def test_walrus_basic_b(items):
    """Duplicate with walrus operator."""
    return __extracted_func_63(items)


def test_with_statement_a(filename):
    """With statement binding."""
    return __extracted_func_75(filename)


def test_with_statement_b(filename):
    """Duplicate with statement."""
    return __extracted_func_75(filename)


def test_with_multiple_a(file1, file2):
    """Multiple context managers."""
    return __extracted_func_83(file1, file2)


def test_with_multiple_b(file1, file2):
    """Duplicate multiple context managers."""
    return __extracted_func_83(file1, file2)


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
    return __extracted_func_38(inner_file, outer_file)


def test_nested_with_b(outer_file, inner_file):
    """Duplicate nested with statements."""
    return __extracted_func_38(inner_file, outer_file)


def test_exception_handler_binding_a(data):
    """Exception variable binding."""
    return __extracted_func_17(data)


def test_exception_handler_binding_b(data):
    """Duplicate exception variable binding."""
    return __extracted_func_17(data)


def test_walrus_while_a(items):
    """Walrus in while loop condition."""
    return __extracted_func_59(items)


def test_walrus_while_b(items):
    """Duplicate walrus in while loop."""
    return __extracted_func_59(items)


def __extracted_func_17(data):
    errors = []
    for item in data:
        try:
            result = int(item)
        except ValueError as e:
            errors.append(str(e))
    return errors


def __extracted_func_38(inner_file, outer_file):
    result = []
    with open(outer_file) as f1:
        result.append(f1.readline())
        with open(inner_file) as f2:
            result.append(f2.readline())
    return result


def __extracted_func_59(items):
    results = []
    idx = 0
    while (item := (items[idx] if idx < len(items) else None)) is not None:
        results.append(item * 2)
        idx += 1
    return results


def __extracted_func_63(items):
    result = []
    if (n := len(items)) > 0:
        result.append(n)
        result.append(n * 2)
    return result


def __extracted_func_75(filename):
    lines = []
    with open(filename) as f:
        for line in f:
            lines.append(line.strip())
    return lines


def __extracted_func_83(file1, file2):
    data = []
    with open(file1) as f1, open(file2) as f2:
        data.append(f1.read())
        data.append(f2.read())
    return data












