"""
Test example: Hygienic naming and name collision avoidance.

Tests that extracted functions use fresh parameter names that don't
collide with variables in the surrounding scope or in the calling context.
"""


def __extracted_func_3(__param_0, data, result):
    for item in data:
        # This block should be extracted, but 'x' might collide
        # Same block - extracted function must avoid collision with 'result'
        x = item * __param_0
        y = x + 10
        z = y ** 2
        result.append(z)
    return result


def __extracted_func_2(__param_0, values, x, y):
    output = []
    for val in values:
        # This block references outer 'x' and 'y'
        # Same structure but different constant
        a = val + x
        b = a * y
        c = b - __param_0
        output.append(c)
    return output


def __extracted_func_1(__param_0, __param_1, data):
    results = []
    for item in data:
        # This block could have naming conflicts with nested functions
        # Different computation, same structure
        a = item ** __param_0
        b = a + __param_1
        c = b * 3
        results.append(c)
    return results


def __extracted_func_0(__param_0, __param_1, cache, data, temp):
    for key, value in data.items():
        # Block that modifies outer 'temp' and 'cache'
        # Same pattern, different validation condition
        processed = __param_0(value)
        validated = len(processed) > __param_1
        if validated:
            temp.append(processed)
            cache[key] = processed
    return (temp, cache)


def process_with_temp_var_v1(data, result):
    """Version 1: Uses 'result' in outer scope, 'x' in duplicate block."""
    result = []
    return __extracted_func_3(2, data, result)


def process_with_temp_var_v2(data, result):
    """Version 2: Same pattern but 'result' is a parameter name."""
    result = []
    return __extracted_func_3(3, data, result)


def calculate_nested_scope_a(values, x, y):
    """Uses x and y as parameters - extracted function must avoid these."""
    return __extracted_func_2(5, values, x, y)


def calculate_nested_scope_b(values, x, y):
    """Same outer variable names - extracted function needs hygienic naming."""
    return __extracted_func_2(10, values, x, y)


def transform_with_shadowing_v1(data, temp, cache):
    """Version 1: Uses 'temp' and 'cache' in outer scope."""
    temp = []
    cache = {}

    return __extracted_func_0(lambda value: value.upper(), 5, cache, data, temp)


def transform_with_shadowing_v2(data, temp, cache):
    """Version 2: Different validation, same structure."""
    temp = []
    cache = {}

    return __extracted_func_0(lambda value: value.lower(), 3, cache, data, temp)


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
        step3 = step2 + param3
        final = step3**2
        results.append(final)
    return results


def nested_function_scope_v1(data, helper, processor):
    """Defines nested functions - extracted code must not collide."""

    def helper(x):
        return x * 2

    def processor(x):
        return x + 5

    return __extracted_func_1(2, 100, data)


def nested_function_scope_v2(data, helper, processor):
    """Same nested function names, different computation."""

    def helper(x):
        return x * 2

    def processor(x):
        return x + 5

    return __extracted_func_1(3, 200, data)
