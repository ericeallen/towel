"""
Adversarial test cases: Exception handling edge cases.

These examples test whether Towel correctly preserves exception semantics
when extracting code with try/except/finally blocks.
"""


def safe_divide_a(x, y):
    """Division with exception handling."""
    result = 0
    try:
        result = x / y
    except ZeroDivisionError:
        result = float("inf")
    return result


def safe_divide_b(a, b):
    """Similar division pattern."""
    return safe_divide_a(a, b)


def parse_int_or_default_a(s, default=0):
    """Parse int with fallback."""
    value = default
    try:
        value = int(s)
    except (ValueError, TypeError):
        value = default
    return value


def parse_int_or_default_b(text, fallback=0):
    """Similar parse pattern."""
    return parse_int_or_default_a(text, fallback)


def process_with_cleanup_a(items):
    """Process with finally cleanup."""
    results = []
    count = 0
    try:
        for item in items:
            count += 1
            results.append(item * 2)
    finally:
        results.append(count)
    return results


def process_with_cleanup_b(values):
    """Similar cleanup pattern."""
    return process_with_cleanup_a(values)


def nested_exception_a(data):
    """Nested exception handling."""
    result = 0
    try:
        try:
            result = int(data)
        except ValueError:
            result = float(data)
    except (ValueError, TypeError):
        result = -1
    return result


def nested_exception_b(input_val):
    """Similar nested exception pattern."""
    return nested_exception_a(input_val)


def exception_with_else_a(items):
    """Exception handling with else clause."""
    results = []
    error = None
    try:
        for item in items:
            results.append(1 / item)
    except ZeroDivisionError as e:
        error = str(e)
    else:
        results.append(999)
    return (results, error)


def exception_with_else_b(values):
    """Similar else clause pattern."""
    return exception_with_else_a(values)


def reraise_exception_a(x):
    """Catch and reraise exception."""
    try:
        result = 10 / x
        return result
    except ZeroDivisionError:
        # Do some logging (simulated)
        x = -1
        raise


def reraise_exception_b(y):
    """Similar reraise pattern."""
    try:
        output = 10 / y
        return output
    except ZeroDivisionError:
        # Do some logging (simulated)
        y = -1
        raise


def multiple_except_a(data):
    """Multiple except clauses."""
    result = None
    try:
        result = int(data) / len(data)
    except ValueError:
        result = -1
    except ZeroDivisionError:
        result = -2
    except TypeError:
        result = -3
    return result


def multiple_except_b(input_val):
    """Similar multiple except pattern."""
    return multiple_except_a(input_val)
