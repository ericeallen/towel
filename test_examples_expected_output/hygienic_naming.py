"""
Test example: Hygienic naming and name collision avoidance.

Tests that extracted functions use fresh parameter names that don't
collide with variables in the surrounding scope or in the calling context.
"""


def process_with_temp_var_v1(data, result):
    """Version 1: Uses 'result' in outer scope, 'x' in duplicate block."""
    result = []
    return extracted_func(2, data, extracted_func, result)


def process_with_temp_var_v2(data, result):
    """Version 2: Same pattern but 'result' is a parameter name."""
    result = []
    return extracted_func(3, data, extracted_func, result)


def calculate_nested_scope_a(values, x, y):
    """Uses x and y as parameters - extracted function must avoid these."""
    return extracted_func(5, values, x, y)


def calculate_nested_scope_b(values, x, y):
    """Same outer variable names - extracted function needs hygienic naming."""
    return extracted_func(10, values, x, y)


def transform_with_shadowing_v1(data, temp, cache):
    """Version 1: Uses 'temp' and 'cache' in outer scope."""
    temp = []
    cache = {}

    for key, value in data.items():
        # Block that modifies outer 'temp' and 'cache'
        processed = value.upper()
        extracted_func(5, cache, key, processed, temp)

    return temp, cache


def transform_with_shadowing_v2(data, temp, cache):
    """Version 2: Different validation, same structure."""
    temp = []
    cache = {}

    for key, value in data.items():
        # Same pattern, different validation condition
        processed = value.lower()
        extracted_func(3, cache, key, processed, temp)

    return temp, cache


def compute_with_param_collision_a(items, param1, param2, param3):
    """Many parameters in outer scope that could collide."""
    results = []
    for item in items:
        # Uses outer params, but extracted function needs different names
        step1 = item + param1
        step2 = step1 * param2
        step3 = step2 - param3
        final = step3**2
        results.append(final)
    return results


def compute_with_param_collision_b(items, param1, param2, param3):
    """Same parameter names, different operations."""
    results = []
    for item in items:
        # Different computation but same structure
        step1 = item - param1
        step2 = step1 / param2
        extracted_func(step2, param3, results)
    return results


def nested_function_scope_v1(data, helper, processor):
    """Defines nested functions - extracted code must not collide."""

    return extracted_func(2, 100, data, extracted_func)


def nested_function_scope_v2(data, helper, processor):
    """Same nested function names, different computation."""

    return extracted_func(3, 200, data, extracted_func)


def extracted_func(__param_0, values, x, y):
    output = []
    for val in values:
        a = val + x
        b = a * y
        c = b - __param_0
        output.append(c)
    return output


def extracted_func():

    def helper(x):
        return x * 2

    def processor(x):
        return x + 5


def extracted_func(__param_0, cache, key, processed, temp):
    validated = len(processed) > __param_0
    if validated:
        temp.append(processed)
        cache[key] = processed


def extracted_func(result, x):
    extracted_func(x, 10, result)


def extracted_func(__param_0, data, extracted_func, result):
    for item in data:
        x = item * __param_0
        extracted_func(result, x)
    return result


def extracted_func(__param_0, __param_1, __param_2):
    step3 = __param_0 + __param_1
    final = step3 ** 2
    __param_2.append(final)


def extracted_func(__param_0, a, results):
    b = a + __param_0
    c = b * 3
    results.append(c)


def extracted_func(__param_0, __param_1, data, extracted_func):
    extracted_func()
    results = []
    for item in data:
        a = item ** __param_0
        extracted_func(__param_1, a, results)
    return results
















