"""
Test example: Nested data structures as parameters.

Tests that the unifier can parameterize complex nested structures including
lists, dicts, comprehensions, and deeply nested expressions.
"""


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
    processed = [x * 2 for x in data if x > 0]
    filtered = [item for item in processed if filter_func(item)]
    result = {
        "values": filtered,
        "count": len(filtered),
        "sum": sum(filtered)
    }
    return result


def transform_with_comprehension_b(data, filter_func):
    """Version B: Different multiplier in comprehension."""
    processed = [x * 3 for x in data if x > 0]
    filtered = [item for item in processed if filter_func(item)]
    result = {
        "values": filtered,
        "count": len(filtered),
        "sum": sum(filtered)
    }
    return result


def build_complex_structure_v1(items, metadata):
    """Version 1: Builds complex nested structure."""
    return __extracted_func_339(2, items)


def build_complex_structure_v2(items, metadata):
    """Version 2: Different multiplier, same structure building."""
    return __extracted_func_339(3, items)


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
    result = {}

    for key in dict1.keys():
        # Complex nested merge
        if key in dict2:
            result[key] = {
                "a": dict1[key]["values"],
                "b": dict2[key]["values"],
                "merged": merger(dict1[key]["values"], dict2[key]["values"])
            }
        else:
            result[key] = dict1[key]

    return result


def merge_nested_dicts_v2(dict1, dict2, merger):
    """Version 2: Different key names, same merge pattern."""
    result = {}

    for key in dict1.keys():
        # Same pattern, different keys
        if key in dict2:
            result[key] = {
                "first": dict1[key]["data"],
                "second": dict2[key]["data"],
                "merged": merger(dict1[key]["data"], dict2[key]["data"])
            }
        else:
            result[key] = dict1[key]

    return result


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
    output = {
        "strings": [],
        "numbers": [],
        "lists": [],
        "total": 0
    }

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
    result = []

    for item in data:
        # Chain of nested operations
        stage1 = {k: v * 2 for k, v in item.items()}
        stage2 = {k: processor(v) for k, v in stage1.items()}
        stage3 = [v for v in stage2.values() if v > 10]
        if stage3:
            result.append({"processed": stage3, "count": len(stage3)})

    return result


def chain_nested_operations_v2(data, processor):
    """Version 2: Different multiplier, same chaining."""
    result = []

    for item in data:
        # Same chain, different multiplier
        stage1 = {k: v * 3 for k, v in item.items()}
        stage2 = {k: processor(v) for k, v in stage1.items()}
        stage3 = [v for v in stage2.values() if v > 10]
        if stage3:
            result.append({"processed": stage3, "count": len(stage3)})

    return result


def __extracted_func_339(__param_0, items):
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


