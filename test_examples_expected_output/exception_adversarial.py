"""
Adversarial test cases: Exception handling edge cases.

These examples test whether Towel correctly preserves exception semantics
when extracting code with try/except/finally blocks.
"""


def __extracted_func_5(__param_0, __param_1, _towel_owner):
    value = __param_0
    try:
        value = int(__param_1)
    except (ValueError, TypeError):
        value = __param_0
    return value


def __extracted_func_4(__param_0, __param_1, _towel_owner):
    result = 0
    try:
        result = __param_0 / __param_1
    except ZeroDivisionError:
        result = float('inf')
    return result


def __extracted_func_3(__param_0, _towel_owner):
    result = 0
    try:
        try:
            result = int(__param_0)
        except ValueError:
            result = float(__param_0)
    except (ValueError, TypeError):
        result = -1
    return result


def __extracted_func_2(__param_0, _towel_owner):
    results = []
    count = 0
    try:
        for item in __param_0:
            count += 1
            results.append(item * 2)
    finally:
        results.append(count)
    return results


def __extracted_func_1(__param_0, _towel_owner):
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


def __extracted_func_0(__param_0, _towel_owner):
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


def safe_divide_a(x, y):
    """Division with exception handling."""
    _towel_arguments_4 = [(y, x)]
    del x
    del y
    return __extracted_func_4(_towel_arguments_4[0][1], _towel_arguments_4[0][0], _towel_arguments_4.pop())


def safe_divide_b(a, b):
    """Similar division pattern."""
    _towel_arguments_4 = [(b, a)]
    del a
    del b
    return __extracted_func_4(_towel_arguments_4[0][1], _towel_arguments_4[0][0], _towel_arguments_4.pop())


def parse_int_or_default_a(s, default=0):
    """Parse int with fallback."""
    _towel_arguments_5 = [(default, s)]
    del s
    del default
    return __extracted_func_5(_towel_arguments_5[0][0], _towel_arguments_5[0][1], _towel_arguments_5.pop())


def parse_int_or_default_b(text, fallback=0):
    """Similar parse pattern."""
    _towel_arguments_5 = [(fallback, text)]
    del text
    del fallback
    return __extracted_func_5(_towel_arguments_5[0][0], _towel_arguments_5[0][1], _towel_arguments_5.pop())


def process_with_cleanup_a(items):
    """Process with finally cleanup."""
    _towel_arguments_2 = [(items,)]
    del items
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def process_with_cleanup_b(values):
    """Similar cleanup pattern."""
    _towel_arguments_2 = [(values,)]
    del values
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def nested_exception_a(data):
    """Nested exception handling."""
    _towel_arguments_3 = [(data,)]
    del data
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def nested_exception_b(input_val):
    """Similar nested exception pattern."""
    _towel_arguments_3 = [(input_val,)]
    del input_val
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def exception_with_else_a(items):
    """Exception handling with else clause."""
    _towel_arguments = [(items,)]
    del items
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def exception_with_else_b(values):
    """Similar else clause pattern."""
    _towel_arguments = [(values,)]
    del values
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


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
    _towel_arguments_1 = [(data,)]
    del data
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def multiple_except_b(input_val):
    """Similar multiple except pattern."""
    _towel_arguments_1 = [(input_val,)]
    del input_val
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())
