"""
Test example: Nested data structures as parameters.

Tests that the unifier can parameterize complex nested structures including
lists, dicts, comprehensions, and deeply nested expressions.
"""


def __extracted_func_3(__param_0, data, filter_func, _towel_owner):
    processed = [x * __param_0 for x in data if x > 0]
    filtered = [item for item in processed if filter_func(item)]
    result = {'values': filtered, 'count': len(filtered), 'sum': sum(filtered)}
    return result


def __extracted_func_2(__param_0, __param_1, __param_2, dict1, dict2, key, merger, result):
    if key in dict2:
        result[key] = {__param_0: dict1[key][__param_2], __param_1: dict2[key][__param_2], 'merged': merger(dict1[key][__param_2], dict2[key][__param_2])}
    else:
        result[key] = dict1[key]


def __extracted_func_1(__param_0, data, processor, _towel_owner):
    result = []
    for item in data:
        # Chain of nested operations
        # Same chain, different multiplier
        stage1 = {k: v * __param_0 for k, v in item.items()}
        stage2 = {k: processor(v) for k, v in stage1.items()}
        stage3 = [v for v in stage2.values() if v > 10]
        if stage3:
            result.append({'processed': stage3, 'count': len(stage3)})
    return result


def __extracted_func_0(__param_0, items, _towel_owner):
    output = {'data': [], 'meta': {'total': 0, 'categories': {}}}
    for item in items:
        # Builds nested structure
        # Same structure, different multiplier
        category = item['category']
        value = item['value'] * __param_0
        output['data'].append({'cat': category, 'val': value})
        output['meta']['total'] += value
        if category not in output['meta']['categories']:
            output['meta']['categories'][category] = 0
        output['meta']['categories'][category] += 1
    return output


def process_nested_dict_v1(data, config):
    """Version 1: Nested dictionary access."""
    results = []
    for item in data:
        # Complex nested structure access
        value = item["user"]["profile"]["settings"]["threshold"]
        adjusted = value * 1.5 + 10
        validated = adjusted > config["min_value"]
        if validated:
            results.append(adjusted)
    return results


def process_nested_dict_v2(data, config):
    """Version 2: Different nested path, same pattern."""
    results = []
    for item in data:
        # Different nesting, same algorithm
        value = item["customer"]["account"]["preferences"]["limit"]
        adjusted = value * 1.5 + 10
        validated = adjusted > config["min_value"]
        if validated:
            results.append(adjusted)
    return results


def transform_with_comprehension_a(data, filter_func):
    """Version A: List comprehension as expression."""
    _towel_arguments_2 = [(filter_func, data)]
    del data
    del filter_func
    return __extracted_func_3(2, _towel_arguments_2[0][1], _towel_arguments_2[0][0], _towel_arguments_2.pop())


def transform_with_comprehension_b(data, filter_func):
    """Version B: Different multiplier in comprehension."""
    _towel_arguments_2 = [(filter_func, data)]
    del data
    del filter_func
    return __extracted_func_3(3, _towel_arguments_2[0][1], _towel_arguments_2[0][0], _towel_arguments_2.pop())


def build_complex_structure_v1(items, metadata):
    """Version 1: Builds complex nested structure."""
    _towel_arguments = [(metadata, items)]
    del items
    del metadata
    return __extracted_func_0(2, _towel_arguments[0][1], _towel_arguments.pop())


def build_complex_structure_v2(items, metadata):
    """Version 2: Different multiplier, same structure building."""
    _towel_arguments = [(metadata, items)]
    del items
    del metadata
    return __extracted_func_0(3, _towel_arguments[0][1], _towel_arguments.pop())


def filter_nested_lists_a(matrix, threshold):
    """Version A: Nested list operations."""
    result = []
    for row in matrix:
        # Nested list comprehension and filtering
        filtered_row = [x for x in row if x > threshold]
        processed = [x**2 for x in filtered_row]
        if sum(processed) > 100:
            result.append(processed)
    return result


def filter_nested_lists_b(matrix, threshold):
    """Version B: Different threshold comparison, same nesting."""
    result = []
    for row in matrix:
        # Same nesting pattern, different operation
        filtered_row = [x for x in row if x < threshold]
        processed = [x**2 for x in filtered_row]
        if sum(processed) > 100:
            result.append(processed)
    return result


def merge_nested_dicts_v1(dict1, dict2, merger):
    """Version 1: Deep dictionary merging."""
    result = {}

    for key in dict1.keys():
        # Complex nested merge
        __extracted_func_2('a', 'b', 'values', dict1, dict2, key, merger, result)

    return result


def merge_nested_dicts_v2(dict1, dict2, merger):
    """Version 2: Different key names, same merge pattern."""
    result = {}

    for key in dict1.keys():
        # Same pattern, different keys
        __extracted_func_2('first', 'second', 'data', dict1, dict2, key, merger, result)

    return result


def process_mixed_types_a(data, converter):
    """Version A: Mixed types in nested structure."""
    output = {"strings": [], "numbers": [], "lists": [], "total": 0}

    for item in data:
        # Type checking and nested appends
        if isinstance(item, str):
            output["strings"].append(item.upper())
        elif isinstance(item, (int, float)):
            output["numbers"].append(item * 2)
            output["total"] += item * 2
        elif isinstance(item, list):
            output["lists"].append([x * 2 for x in item])

    return output


def process_mixed_types_b(data, converter):
    """Version B: Different multiplier, same type handling."""
    output = {"strings": [], "numbers": [], "lists": [], "total": 0}

    for item in data:
        # Same type checking, different multiplier
        if isinstance(item, str):
            output["strings"].append(item.lower())
        elif isinstance(item, (int, float)):
            output["numbers"].append(item * 3)
            output["total"] += item * 3
        elif isinstance(item, list):
            output["lists"].append([x * 3 for x in item])

    return output


def chain_nested_operations_v1(data, processor):
    """Version 1: Chained operations on nested structures."""
    _towel_arguments_1 = [(processor, data)]
    del data
    del processor
    return __extracted_func_1(2, _towel_arguments_1[0][1], _towel_arguments_1[0][0], _towel_arguments_1.pop())


def chain_nested_operations_v2(data, processor):
    """Version 2: Different multiplier, same chaining."""
    _towel_arguments_1 = [(processor, data)]
    del data
    del processor
    return __extracted_func_1(3, _towel_arguments_1[0][1], _towel_arguments_1[0][0], _towel_arguments_1.pop())
