"""
Adversarial test cases: Tricky edge cases that might break extraction.

These examples test really subtle scenarios that could expose bugs:
- Multiple return statements
- Mixed data types and operator overloading
- String formatting and interpolation
- Conditional expressions (ternary operator)
- List/dict modifications vs. reassignment
"""


def __extracted_func_11(__param_0, __param_1, _towel_owner):
    found = False
    found = __param_0 in __param_1
    return found


def __extracted_func_10(__param_0, __param_1, __param_2, _towel_owner):
    result = False
    result = __param_0 < __param_1 < __param_2
    return result


def __extracted_func_9(__param_0, __param_1, _towel_owner):
    result = set()
    result = __param_0 | __param_1
    result = result & {1, 2, 3}
    return len(result)


def __extracted_func_8(__param_0, __param_1, __param_2, _towel_owner):
    result = []
    result = __param_0[__param_1:__param_2]
    result = result + [999]
    return len(result)


def __extracted_func_7(__param_0, __param_1, __param_2, _towel_owner):
    result = False
    result = __param_0 > 0 and __param_1 > 0
    result = result or __param_2 > 0
    return result


def __extracted_func_6(__param_0, __param_1, _towel_owner):
    result = 0
    result = __param_0 if __param_0 > __param_1 else __param_1
    result = result * 2
    return result


def __extracted_func_5(__param_0, __param_1, _towel_owner):
    message = ''
    message = f'Name: {__param_0}'
    message = message + f', Age: {__param_1}'
    return message


def __extracted_func_4(__param_0, __param_1, _towel_owner):
    result = {}
    for key, value in __param_0.items():
        result[key] = value
    result.update(__param_1)
    return len(result)


def __extracted_func_3(__param_0, __param_1, _towel_owner):
    result = []
    for item in __param_0:
        result.append(item)
    result.extend(__param_1)
    return len(result)


def __extracted_func_2(__param_0, __param_1, _towel_owner):
    result = __param_0 * 2
    if result > __param_1:
        return result
    result = result + 10
    return result


def __extracted_func_1(__param_0, _towel_owner):
    if not __param_0:
        return 0
    total = 0
    for item in __param_0:
        total += item
    return total


def __extracted_func_0(__param_0, _towel_owner):
    result = 0
    if isinstance(__param_0, int):
        result = __param_0 * 2
    elif isinstance(__param_0, str):
        result = len(__param_0)
    else:
        result = -1
    return result


def conditional_return_a(x, threshold):
    """Multiple return points."""
    _towel_arguments_2 = [(threshold, x)]
    del x
    del threshold
    return __extracted_func_2(_towel_arguments_2[0][1], _towel_arguments_2[0][0], _towel_arguments_2.pop())


def conditional_return_b(y, limit):
    """Similar multiple return pattern."""
    _towel_arguments_2 = [(limit, y)]
    del y
    del limit
    return __extracted_func_2(_towel_arguments_2[0][1], _towel_arguments_2[0][0], _towel_arguments_2.pop())


def early_return_a(items):
    """Early return for empty case."""
    _towel_arguments_1 = [(items,)]
    del items
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def early_return_b(values):
    """Similar early return pattern."""
    _towel_arguments_1 = [(values,)]
    del values
    return __extracted_func_1(_towel_arguments_1[0][0], _towel_arguments_1.pop())


def string_formatting_a(name, age):
    """String formatting operations."""
    _towel_arguments_5 = [(age, name)]
    del name
    del age
    return __extracted_func_5(_towel_arguments_5[0][1], _towel_arguments_5[0][0], _towel_arguments_5.pop())


def string_formatting_b(title, count):
    """Similar string formatting."""
    _towel_arguments_5 = [(count, title)]
    del title
    del count
    return __extracted_func_5(_towel_arguments_5[0][1], _towel_arguments_5[0][0], _towel_arguments_5.pop())


def ternary_expression_a(x, y):
    """Conditional expressions."""
    _towel_arguments_6 = [(y, x)]
    del x
    del y
    return __extracted_func_6(_towel_arguments_6[0][1], _towel_arguments_6[0][0], _towel_arguments_6.pop())


def ternary_expression_b(a, b):
    """Similar ternary pattern."""
    _towel_arguments_6 = [(b, a)]
    del a
    del b
    return __extracted_func_6(_towel_arguments_6[0][1], _towel_arguments_6[0][0], _towel_arguments_6.pop())


def list_extend_vs_assign_a(items, extra):
    """Tests list modification semantics."""
    _towel_arguments_3 = [(extra, items)]
    del items
    del extra
    return __extracted_func_3(_towel_arguments_3[0][1], _towel_arguments_3[0][0], _towel_arguments_3.pop())


def list_extend_vs_assign_b(values, additional):
    """Similar list modification."""
    _towel_arguments_3 = [(additional, values)]
    del values
    del additional
    return __extracted_func_3(_towel_arguments_3[0][1], _towel_arguments_3[0][0], _towel_arguments_3.pop())


def dict_update_a(base, updates):
    """Dictionary update operations."""
    _towel_arguments_4 = [(updates, base)]
    del base
    del updates
    return __extracted_func_4(_towel_arguments_4[0][1], _towel_arguments_4[0][0], _towel_arguments_4.pop())


def dict_update_b(initial, changes):
    """Similar dict update pattern."""
    _towel_arguments_4 = [(changes, initial)]
    del initial
    del changes
    return __extracted_func_4(_towel_arguments_4[0][1], _towel_arguments_4[0][0], _towel_arguments_4.pop())


def boolean_logic_a(x, y, z):
    """Complex boolean expressions."""
    _towel_arguments_7 = [(z, y, x)]
    del x
    del y
    del z
    return __extracted_func_7(_towel_arguments_7[0][2], _towel_arguments_7[0][1], _towel_arguments_7[0][0], _towel_arguments_7.pop())


def boolean_logic_b(a, b, c):
    """Similar boolean logic."""
    _towel_arguments_7 = [(c, b, a)]
    del a
    del b
    del c
    return __extracted_func_7(_towel_arguments_7[0][2], _towel_arguments_7[0][1], _towel_arguments_7[0][0], _towel_arguments_7.pop())


def chained_comparisons_a(x, lower, upper):
    """Chained comparison operators."""
    _towel_arguments_10 = [(upper, lower, x)]
    del x
    del lower
    del upper
    return __extracted_func_10(_towel_arguments_10[0][1], _towel_arguments_10[0][2], _towel_arguments_10[0][0], _towel_arguments_10.pop())


def chained_comparisons_b(y, min_val, max_val):
    """Similar chained comparison."""
    _towel_arguments_10 = [(max_val, min_val, y)]
    del y
    del min_val
    del max_val
    return __extracted_func_10(_towel_arguments_10[0][1], _towel_arguments_10[0][2], _towel_arguments_10[0][0], _towel_arguments_10.pop())


def mixed_types_a(value):
    """Operations on mixed types."""
    _towel_arguments = [(value,)]
    del value
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def mixed_types_b(item):
    """Similar mixed type handling."""
    _towel_arguments = [(item,)]
    del item
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def slice_operations_a(items, start, end):
    """List slicing operations."""
    _towel_arguments_8 = [(end, start, items)]
    del items
    del start
    del end
    return __extracted_func_8(_towel_arguments_8[0][2], _towel_arguments_8[0][1], _towel_arguments_8[0][0], _towel_arguments_8.pop())


def slice_operations_b(values, begin, finish):
    """Similar slice pattern."""
    _towel_arguments_8 = [(finish, begin, values)]
    del values
    del begin
    del finish
    return __extracted_func_8(_towel_arguments_8[0][2], _towel_arguments_8[0][1], _towel_arguments_8[0][0], _towel_arguments_8.pop())


def membership_test_a(item, collection):
    """Membership testing."""
    _towel_arguments_11 = [(collection, item)]
    del item
    del collection
    return __extracted_func_11(_towel_arguments_11[0][1], _towel_arguments_11[0][0], _towel_arguments_11.pop())


def membership_test_b(element, group):
    """Similar membership test."""
    _towel_arguments_11 = [(group, element)]
    del element
    del group
    return __extracted_func_11(_towel_arguments_11[0][1], _towel_arguments_11[0][0], _towel_arguments_11.pop())


def set_operations_a(set1, set2):
    """Set union/intersection."""
    _towel_arguments_9 = [(set2, set1)]
    del set1
    del set2
    return __extracted_func_9(_towel_arguments_9[0][1], _towel_arguments_9[0][0], _towel_arguments_9.pop())


def set_operations_b(group1, group2):
    """Similar set operations."""
    _towel_arguments_9 = [(group2, group1)]
    del group1
    del group2
    return __extracted_func_9(_towel_arguments_9[0][1], _towel_arguments_9[0][0], _towel_arguments_9.pop())
