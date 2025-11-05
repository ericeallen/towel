"""
Adversarial test cases: Exception handling edge cases.

These examples test whether Towel correctly preserves exception semantics
when extracting code with try/except/finally blocks.
"""


def safe_divide_a(x, y):
    """Division with exception handling."""
    return __extracted_func_477(x, y)


def safe_divide_b(a, b):
    """Similar division pattern."""
    return __extracted_func_477(a, b)


def parse_int_or_default_a(s, default=0):
    """Parse int with fallback."""
    return __extracted_func_481(default, s)


def parse_int_or_default_b(text, fallback=0):
    """Similar parse pattern."""
    return __extracted_func_481(fallback, text)


def process_with_cleanup_a(items):
    """Process with finally cleanup."""
    return __extracted_func_465(items)


def process_with_cleanup_b(values):
    """Similar cleanup pattern."""
    return __extracted_func_465(values)


def nested_exception_a(data):
    """Nested exception handling."""
    return __extracted_func_475(data)


def nested_exception_b(input_val):
    """Similar nested exception pattern."""
    return __extracted_func_475(input_val)


def exception_with_else_a(items):
    """Exception handling with else clause."""
    return __extracted_func_443(items)


def exception_with_else_b(values):
    """Similar else clause pattern."""
    return __extracted_func_443(values)


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
    return __extracted_func_459(data)


def multiple_except_b(input_val):
    """Similar multiple except pattern."""
    return __extracted_func_459(input_val)


def __extracted_func_443(__param_0):
    results = []
    error = None
    try:
        for item in __param_0:
            results.append(1 / item)
    except ZeroDivisionError as e:
        error = str(e)
    else:
        results.append(999)
    return (results, error)


def __extracted_func_459(__param_0):
    result = None
    try:
        result = int(__param_0) / len(__param_0)
    except ValueError:
        result = -1
    except ZeroDivisionError:
        result = -2
    except TypeError:
        result = -3
    return result


def __extracted_func_465(__param_0):
    results = []
    count = 0
    try:
        for item in __param_0:
            count += 1
            results.append(item * 2)
    finally:
        results.append(count)
    return results


def __extracted_func_475(__param_0):
    result = 0
    try:
        try:
            result = int(__param_0)
        except ValueError:
            result = float(__param_0)
    except (ValueError, TypeError):
        result = -1
    return result


def __extracted_func_477(__param_0, __param_1):
    result = 0
    try:
        result = __param_0 / __param_1
    except ZeroDivisionError:
        result = float('inf')
    return result


def __extracted_func_481(__param_0, __param_1):
    value = __param_0
    try:
        value = int(__param_1)
    except (ValueError, TypeError):
        value = __param_0
    return value












