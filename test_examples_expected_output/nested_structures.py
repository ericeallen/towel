"""
Test example: Nested data structures as parameters.

Tests that the unifier can parameterize complex nested structures including
lists, dicts, comprehensions, and deeply nested expressions.
"""


def process_nested_dict_v1(data, config):
    """Version 1: Nested dictionary access."""
    return __extracted_func_1237('user', 'profile', 'settings', 'threshold', config, data)


def process_nested_dict_v2(data, config):
    """Version 2: Different nested path, same pattern."""
    return __extracted_func_1237('customer', 'account', 'preferences', 'limit', config, data)


def transform_with_comprehension_a(data, filter_func):
    """Version A: List comprehension as expression."""
    return __extracted_func_1252(2, data, filter_func)


def transform_with_comprehension_b(data, filter_func):
    """Version B: Different multiplier in comprehension."""
    return __extracted_func_1252(3, data, filter_func)


def build_complex_structure_v1(items, metadata):
    """Version 1: Builds complex nested structure."""
    return __extracted_func_1161(2, items)


def build_complex_structure_v2(items, metadata):
    """Version 2: Different multiplier, same structure building."""
    return __extracted_func_1161(3, items)


def filter_nested_lists_a(matrix, threshold):
    """Version A: Nested list operations."""
    result = []
    for row in matrix:
        # Nested list comprehension and filtering
        filtered_row = [x for x in row if x > threshold]
        processed = [x ** 2 for x in filtered_row]
        if sum(processed) > 100:
            result.append(processed)
    return result


def filter_nested_lists_b(matrix, threshold):
    """Version B: Different threshold comparison, same nesting."""
    result = []
    for row in matrix:
        # Same nesting pattern, different operation
        filtered_row = [x for x in row if x < threshold]
        processed = [x ** 2 for x in filtered_row]
        if sum(processed) > 100:
            result.append(processed)
    return result


def merge_nested_dicts_v1(dict1, dict2, merger):
    """Version 1: Deep dictionary merging."""
    return __extracted_func_1201('a', 'b', 'values', dict1, dict2, merger)


def merge_nested_dicts_v2(dict1, dict2, merger):
    """Version 2: Different key names, same merge pattern."""
    return __extracted_func_1201('first', 'second', 'data', dict1, dict2, merger)


def process_mixed_types_a(data, converter):
    """Version A: Mixed types in nested structure."""
    output = {
        "strings": [],
        "numbers": [],
        "lists": [],
        "total": 0
    }

    for item in data:
        # Type checking and nested appends
        __extracted_func_1262(lambda *args, **kwargs: item.upper(*args, **kwargs), 2, item, output)

    return output


def process_mixed_types_b(data, converter):
    """Version B: Different multiplier, same type handling."""
    output = {
        "strings": [],
        "numbers": [],
        "lists": [],
        "total": 0
    }

    for item in data:
        # Same type checking, different multiplier
        __extracted_func_1262(lambda *args, **kwargs: item.lower(*args, **kwargs), 3, item, output)

    return output


def chain_nested_operations_v1(data, processor):
    """Version 1: Chained operations on nested structures."""
    return __extracted_func_1231(2, data, processor)


def chain_nested_operations_v2(data, processor):
    """Version 2: Different multiplier, same chaining."""
    return __extracted_func_1231(3, data, processor)


def __extracted_func_1161(__param_0, items):
    output = {'data': [], 'meta': {'total': 0, 'categories': {}}}
    for item in items:
        category = item['category']
        value = item['value'] * __param_0
        output['data'].append({'cat': category, 'val': value})
        output['meta']['total'] += value
        if category not in output['meta']['categories']:
            output['meta']['categories'][category] = 0
        output['meta']['categories'][category] += 1
    return output


def __extracted_func_1201(__param_0, __param_1, __param_2, dict1, dict2, merger):
    result = {}
    for key in dict1.keys():
        if key in dict2:
            result[key] = {__param_0: dict1[key][__param_2], __param_1: dict2[key][__param_2], 'merged': merger(dict1[key][__param_2], dict2[key][__param_2])}
        else:
            result[key] = dict1[key]
    return result


def __extracted_func_1231(__param_0, data, processor):
    result = []
    for item in data:
        stage1 = {k: v * __param_0 for k, v in item.items()}
        stage2 = {k: processor(v) for k, v in stage1.items()}
        stage3 = [v for v in stage2.values() if v > 10]
        if stage3:
            result.append({'processed': stage3, 'count': len(stage3)})
    return result


def __extracted_func_1237(__param_0, __param_1, __param_2, __param_3, config, data):
    results = []
    for item in data:
        value = item[__param_0][__param_1][__param_2][__param_3]
        adjusted = value * 1.5 + 10
        validated = adjusted > config['min_value']
        if validated:
            results.append(adjusted)
    return results


def __extracted_func_1252(__param_0, data, filter_func):
    processed = [x * __param_0 for x in data if x > 0]
    filtered = [item for item in processed if filter_func(item)]
    result = {'values': filtered, 'count': len(filtered), 'sum': sum(filtered)}
    return result


def __extracted_func_1262(__param_0, __param_1, item, output):
    if isinstance(item, str):
        output['strings'].append(__param_0())
    elif isinstance(item, (int, float)):
        output['numbers'].append(item * __param_1)
        output['total'] += item * __param_1
    elif isinstance(item, list):
        output['lists'].append([x * __param_1 for x in item])












