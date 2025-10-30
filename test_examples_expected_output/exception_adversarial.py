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
        result = float('inf')
    return result


def safe_divide_b(a, b):
    """Similar division pattern."""
    output = 0
    try:
        output = a / b
    except ZeroDivisionError:
        output = float('inf')
    return output


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
    result = fallback
    try:
        result = int(text)
    except (ValueError, TypeError):
        result = fallback
    return result


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
    output = []
    total = 0
    try:
        for value in values:
            total += 1
            output.append(value * 2)
    finally:
        output.append(total)
    return output


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
    output = 0
    try:
        try:
            output = int(input_val)
        except ValueError:
            output = float(input_val)
    except (ValueError, TypeError):
        output = -1
    return output


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
    output = []
    err = None
    try:
        for value in values:
            output.append(1 / value)
    except ZeroDivisionError as e:
        err = str(e)
    else:
        output.append(999)
    return (output, err)


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
    output = None
    try:
        output = int(input_val) / len(input_val)
    except ValueError:
        output = -1
    except ZeroDivisionError:
        output = -2
    except TypeError:
        output = -3
    return output
