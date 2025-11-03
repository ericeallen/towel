"""
Test example: Lambda expressions and functional programming patterns.

Tests that the unifier handles lambdas, higher-order functions,
map/filter/reduce patterns, and functional composition.
"""


def apply_transformation_v1(data, multiplier):
    """Version 1: Lambda with captured variable."""
    # Lambda that captures multiplier
    return __extracted_func_926(10, data, multiplier)


def apply_transformation_v2(data, multiplier):
    """Version 2: Different offset, same lambda pattern."""
    # Same pattern, different offset
    return __extracted_func_926(20, data, multiplier)


def reduce_with_lambda_a(values, initial, combiner):
    """Version A: Reduce with lambda."""
    return __extracted_func_594(10, initial, values)


def reduce_with_lambda_b(values, initial, combiner):
    """Version B: Different threshold, same reduce pattern."""
    return __extracted_func_594(20, initial, values)


def chain_functional_ops_v1(data, filter_func, map_func):
    """Version 1: Chained functional operations."""
    # Functional pipeline
    return __extracted_func_881(2, data, filter_func, map_func)


def chain_functional_ops_v2(data, filter_func, map_func):
    """Version 2: Different multiplier, same pipeline."""
    # Same pipeline, different multiplier
    return __extracted_func_881(3, data, filter_func, map_func)


def higher_order_function_a(data, threshold):
    """Version A: Returns and uses functions."""
    # Create higher-order functions
    return __extracted_func_484(data, threshold)


def higher_order_function_b(data, threshold):
    """Version B: Different limit, same higher-order pattern."""
    # Same pattern, different limit
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    return __extracted_func_905(10, data, make_validator, threshold)


def higher_order_function_c(data, threshold):
    """Version C: Same as A but with identical usage (no parameter differences)."""
    # Create higher-order functions
    return __extracted_func_484(data, threshold)


def higher_order_function_d(data, threshold):
    """Version D: Same as C - identical nested function usage."""
    # Create higher-order functions
    return __extracted_func_780(data, threshold)


def compose_functions_v1(data, f, g, h):
    """Version 1: Function composition."""
    # Compose functions
    return __extracted_func_830(2, data, f, g, h)


def compose_functions_v2(data, f, g, h):
    """Version 2: Different multiplier, same composition."""
    # Same composition
    return __extracted_func_830(3, data, f, g, h)


def partial_application_a(values, base_func, modifier):
    """Version A: Partial function application."""
    # Partial application
    return __extracted_func_867('config1', base_func, modifier, values)


def partial_application_b(values, base_func, modifier):
    """Version B: Different config, same partial application."""
    # Same partial application pattern
    return __extracted_func_867('config2', base_func, modifier, values)


def curry_functions_v1(data, operation, param1, param2):
    """Version 1: Currying pattern."""
    # Curried functions
    return __extracted_func_730(2, data, operation, param1, param2)


def curry_functions_v2(data, operation, param1, param2):
    """Version 2: Different multiplier, same currying."""
    # Same currying pattern
    return __extracted_func_730(3, data, operation, param1, param2)


def generator_with_lambda_a(data, predicate):
    """Version A: Generator with lambda."""
    # Generator expression with lambda
    transformed = (item * 2 for item in data if predicate(item))
    return __extracted_func_921(transformed)


def generator_with_lambda_b(data, predicate):
    """Version B: Different multiplier, same generator pattern."""
    # Same generator pattern
    transformed = (item * 3 for item in data if predicate(item))
    return __extracted_func_921(transformed)


def __extracted_func_484(data, threshold):

    return __extracted_func_780(data, threshold)


def __extracted_func_594(__param_0, initial, values):
    from functools import reduce
    result = reduce(lambda acc, x: acc + (x ** 2 if x > __param_0 else x), values, initial)
    normalized = list(map(lambda x: x / result if result != 0 else 0, values))
    return {'reduced': result, 'normalized': normalized}


def __extracted_func_730(__param_0, data, operation, param1, param2):
    curried = lambda a: lambda b: lambda c: operation(a, b, c)
    stage1 = curried(param1)
    stage2 = stage1(param2)
    results = []
    for item in data:
        result = stage2(item * __param_0)
        if result > 0:
            results.append(result)
    return results


def __extracted_func_780(data, threshold):

    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    return __extracted_func_905(5, data, make_validator, threshold)


def __extracted_func_830(__param_0, data, f, g, h):
    compose = lambda x: h(g(f(x)))
    results = []
    for item in data:
        try:
            result = compose(item * __param_0)
            if result is not None:
                results.append(result)
        except Exception:
            continue
    return results


def __extracted_func_867(__param_0, base_func, modifier, values):
    apply_modifier = lambda x: base_func(x, modifier, __param_0)
    processed = list(map(apply_modifier, values))
    filtered = list(filter(lambda x: x > 100, processed))
    return {'processed': processed, 'filtered': filtered, 'count': len(filtered)}


def __extracted_func_881(__param_0, data, filter_func, map_func):
    step1 = filter(lambda x: x is not None and x > 0, data)
    step2 = map(lambda x: x * __param_0, step1)
    step3 = filter(filter_func, step2)
    step4 = map(map_func, step3)
    result = list(step4)
    if result:
        return sorted(result, key=lambda x: (x % 10, x))
    return []


def __extracted_func_905(__param_0, data, make_validator, threshold):

    def make_transformer(factor):
        return lambda x: x * factor + threshold
    validator = make_validator(__param_0)
    transformer = make_transformer(2)
    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))
    return transformed


def __extracted_func_921(transformed):
    filtered = (x for x in transformed if x > 10)
    squared = (x ** 2 for x in filtered)
    result = list(squared)
    if result:
        return sorted(result, key=lambda x: -x)[:100]
    return []


def __extracted_func_926(__param_0, data, multiplier):
    transform = lambda x: x * multiplier + __param_0
    filtered = filter(lambda x: x > 0, data)
    result = list(map(transform, filtered))
    if len(result) > 0:
        return sorted(result, key=lambda x: x, reverse=True)
    return []




















