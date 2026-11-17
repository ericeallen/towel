"""
Test example: Nested data structures as parameters.

Tests that the unifier can parameterize complex nested structures including
lists, dicts, comprehensions, and deeply nested expressions.
"""


def __extracted_func_5(data, filter_func, _param_0):
    processed = [x * _param_0 for x in data if x > 0]
    filtered = [item for item in processed if filter_func(item)]
    result = {"values": filtered, "count": len(filtered), "sum": sum(filtered)}
    return result


def __extracted_func_4(data, config, _param_0, _param_1, _param_2, _param_3):
    results = []
    for item in data:
        # Complex nested structure access
        # Different nesting, same algorithm
        value = item[_param_0][_param_1][_param_2][_param_3]
        adjusted = value * 1.5 + 10
        validated = adjusted > config["min_value"]
        if validated:
            results.append(adjusted)
    return results


def __extracted_func_3(data, processor, _param_0):
    result = []
    for item in data:
        # Chain of nested operations
        # Same chain, different multiplier
        stage1 = {k: v * _param_0 for k, v in item.items()}
        stage2 = {k: processor(v) for k, v in stage1.items()}
        stage3 = [v for v in stage2.values() if v > 10]
        if stage3:
            result.append({"processed": stage3, "count": len(stage3)})

    return result


def __extracted_func_2(data, _param_0, _param_1):
    output = {"strings": [], "numbers": [], "lists": [], "total": 0}
    for item in data:
        # Type checking and nested appends
        # Same type checking, different multiplier
        if isinstance(item, str):
            output["strings"].append(_param_0(item))
        elif isinstance(item, (int, float)):
            output["numbers"].append(item * _param_1)
            output["total"] += item * _param_1
        elif isinstance(item, list):
            output["lists"].append([x * _param_1 for x in item])

    return output


def __extracted_func_1(items, _param_0):
    output = {"data": [], "meta": {"total": 0, "categories": {}}}
    for item in items:
        # Builds nested structure
        # Same structure, different multiplier
        category = item["category"]
        value = item["value"] * _param_0
        output["data"].append({"cat": category, "val": value})
        output["meta"]["total"] += value
        if category not in output["meta"]["categories"]:
            output["meta"]["categories"][category] = 0
        output["meta"]["categories"][category] += 1

    return output


def __extracted_func_0(dict1, dict2, merger, _param_0, _param_2, _param_1):
    result = {}
    for key in dict1.keys():
        # Complex nested merge
        # Same pattern, different keys
        if key in dict2:
            result[key] = {
                _param_0: dict1[key][_param_2],
                _param_1: dict2[key][_param_2],
                "merged": merger(dict1[key][_param_2], dict2[key][_param_2]),
            }
        else:
            result[key] = dict1[key]

    return result


def process_nested_dict_v1(data, config):
    """Version 1: Nested dictionary access."""
    return __extracted_func_4(data, config, "user", "profile", "settings", "threshold")


def process_nested_dict_v2(data, config):
    """Version 2: Different nested path, same pattern."""
    return __extracted_func_4(data, config, "customer", "account", "preferences", "limit")


def transform_with_comprehension_a(data, filter_func):
    """Version A: List comprehension as expression."""
    return __extracted_func_5(data, filter_func, 2)


def transform_with_comprehension_b(data, filter_func):
    """Version B: Different multiplier in comprehension."""
    return __extracted_func_5(data, filter_func, 3)


def build_complex_structure_v1(items, metadata):
    """Version 1: Builds complex nested structure."""
    return __extracted_func_1(items, 2)


def build_complex_structure_v2(items, metadata):
    """Version 2: Different multiplier, same structure building."""
    return __extracted_func_1(items, 3)


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
    return __extracted_func_0(dict1, dict2, merger, "a", "values", "b")


def merge_nested_dicts_v2(dict1, dict2, merger):
    """Version 2: Different key names, same merge pattern."""
    return __extracted_func_0(dict1, dict2, merger, "first", "data", "second")


def process_mixed_types_a(data, converter):
    """Version A: Mixed types in nested structure."""
    return __extracted_func_2(data, lambda item: item.upper(), 2)


def process_mixed_types_b(data, converter):
    """Version B: Different multiplier, same type handling."""
    return __extracted_func_2(data, lambda item: item.lower(), 3)


def chain_nested_operations_v1(data, processor):
    """Version 1: Chained operations on nested structures."""
    return __extracted_func_3(data, processor, 2)


def chain_nested_operations_v2(data, processor):
    """Version 2: Different multiplier, same chaining."""
    return __extracted_func_3(data, processor, 3)
