"""
Comprehensive test cases for all Python binding constructs.

Tests that Towel correctly handles free variable analysis for:
- Walrus operator (:=)
- With statements (with ... as)
- Async constructs (async for, async with)
"""


def test_walrus_basic_a(items):
    """Walrus operator in if condition."""
    result = []
    if (n := len(items)) > 0:
        result.append(n)
        result.append(n * 2)
    return result


def test_walrus_basic_b(items):
    """Duplicate with walrus operator."""
    result = []
    if (n := len(items)) > 0:
        result.append(n)
        result.append(n * 2)
    return result


def test_with_statement_a(filename):
    """With statement binding."""
    lines = []
    with open(filename) as f:
        for line in f:
            lines.append(line.strip())
    return lines


def test_with_statement_b(filename):
    """Duplicate with statement."""
    lines = []
    with open(filename) as f:
        for line in f:
            lines.append(line.strip())
    return lines


def test_with_multiple_a(file1, file2):
    """Multiple context managers."""
    data = []
    with open(file1) as f1, open(file2) as f2:
        data.append(f1.read())
        data.append(f2.read())
    return data


def test_with_multiple_b(file1, file2):
    """Duplicate multiple context managers."""
    data = []
    with open(file1) as f1, open(file2) as f2:
        data.append(f1.read())
        data.append(f2.read())
    return data


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
    result = []
    with open(outer_file) as f1:
        result.append(f1.readline())
        with open(inner_file) as f2:
            result.append(f2.readline())
    return result


def test_nested_with_b(outer_file, inner_file):
    """Duplicate nested with statements."""
    result = []
    with open(outer_file) as f1:
        result.append(f1.readline())
        with open(inner_file) as f2:
            result.append(f2.readline())
    return result


def test_exception_handler_binding_a(data):
    """Exception variable binding."""
    return __extracted_func_9(data)


def test_exception_handler_binding_b(data):
    """Duplicate exception variable binding."""
    return __extracted_func_9(data)


def test_walrus_while_a(items):
    """Walrus in while loop condition."""
    results = []
    idx = 0
    while (item := items[idx] if idx < len(items) else None) is not None:
        results.append(item * 2)
        idx += 1
    return results


def test_walrus_while_b(items):
    """Duplicate walrus in while loop."""
    results = []
    idx = 0
    while (item := items[idx] if idx < len(items) else None) is not None:
        results.append(item * 2)
        idx += 1
    return results


def __extracted_func_9(data):
    errors = []
    for item in data:
        try:
            result = int(item)
        except ValueError as e:
            errors.append(str(e))
    return errors


