"""
Test example: Lambda expressions and functional programming patterns.

Tests that the unifier handles lambdas, higher-order functions,
map/filter/reduce patterns, and functional composition.
"""


def __extracted_func_7(values, base_func, modifier, _param_0):
    apply_modifier = lambda x: base_func(x, modifier, _param_0)
    # Use partially applied function
    processed = list(map(apply_modifier, values))
    filtered = list(filter(lambda x: x > 100, processed))

    return {"processed": processed, "filtered": filtered, "count": len(filtered)}


def __extracted_func_6(data, multiplier, _param_0):
    transform = lambda x: x * multiplier + _param_0
    filtered = filter(lambda x: x > 0, data)
    result = list(map(transform, filtered))

    if len(result) > 0:
        return sorted(result, key=lambda x: x, reverse=True)
    return []


def __extracted_func_5(data, predicate, _param_0):
    transformed = (item * _param_0 for item in data if predicate(item))
    filtered = (x for x in transformed if x > 10)
    squared = (x**2 for x in filtered)
    # Consume generator
    result = list(squared)
    if result:
        return sorted(result, key=lambda x: -x)[:100]
    return []


def __extracted_func_4(values, initial, _param_0):
    from functools import reduce
    # Complex reduce operation
    # Same pattern, different threshold
    result = reduce(lambda acc, x: acc + (x**2 if x > _param_0 else x), values, initial)
    # Post-process with lambda
    normalized = list(map(lambda x: x / result if result != 0 else 0, values))

    return {"reduced": result, "normalized": normalized}


def __extracted_func_3(data, param1, param2, curried, _param_0):
    stage1 = curried(param1)
    stage2 = stage1(param2)

    results = []
    for item in data:
        result = stage2(item * _param_0)
        if result > 0:
            results.append(result)

    return results


def __extracted_func_2(data, filter_func, map_func, _param_0):
    step1 = filter(lambda x: x is not None and x > 0, data)
    step2 = map(lambda x: x * _param_0, step1)
    step3 = filter(filter_func, step2)
    step4 = map(map_func, step3)
    result = list(step4)
    # Final transformation
    if result:
        return sorted(result, key=lambda x: (x % 10, x))
    return []


def __extracted_func_1(data, f, g, h, _param_0):
    compose = lambda x: h(g(f(x)))

    results = []
    for item in data:
        try:
            result = compose(item * _param_0)
            if result is not None:
                results.append(result)
        except Exception:
            continue

    return results


def __extracted_func_0(data, make_validator, _param_0, make_transformer):
    validator = make_validator(_param_0)
    transformer = make_transformer(2)

    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))

    return transformed


def apply_transformation_v1(data, multiplier):
    """Version 1: Lambda with captured variable."""
    # Lambda that captures multiplier
    return __extracted_func_6(data, multiplier, 10)


def apply_transformation_v2(data, multiplier):
    """Version 2: Different offset, same lambda pattern."""
    # Same pattern, different offset
    return __extracted_func_6(data, multiplier, 20)


def reduce_with_lambda_a(values, initial, combiner):
    """Version A: Reduce with lambda."""
    return __extracted_func_4(values, initial, 10)


def reduce_with_lambda_b(values, initial, combiner):
    """Version B: Different threshold, same reduce pattern."""
    return __extracted_func_4(values, initial, 20)


def chain_functional_ops_v1(data, filter_func, map_func):
    """Version 1: Chained functional operations."""
    # Functional pipeline
    return __extracted_func_2(data, filter_func, map_func, 2)


def chain_functional_ops_v2(data, filter_func, map_func):
    """Version 2: Different multiplier, same pipeline."""
    # Same pipeline, different multiplier
    return __extracted_func_2(data, filter_func, map_func, 3)


def higher_order_function_a(data, threshold):
    """Version A: Returns and uses functions."""

    # Create higher-order functions
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    # Use the functions
    return __extracted_func_0(data, make_validator, 5, make_transformer)


def higher_order_function_b(data, threshold):
    """Version B: Different limit, same higher-order pattern."""

    # Same pattern, different limit
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    # Use the functions
    return __extracted_func_0(data, make_validator, 10, make_transformer)


def higher_order_function_c(data, threshold):
    """Version C: Same as A but with identical usage (no parameter differences)."""

    # Create higher-order functions
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    # Use the functions with SAME arguments as version D
    return __extracted_func_0(data, make_validator, 5, make_transformer)


def higher_order_function_d(data, threshold):
    """Version D: Same as C - identical nested function usage."""

    # Create higher-order functions
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    # Use the functions with SAME arguments as version C
    return __extracted_func_0(data, make_validator, 5, make_transformer)


def compose_functions_v1(data, f, g, h):
    """Version 1: Function composition."""
    # Compose functions
    return __extracted_func_1(data, f, g, h, 2)


def compose_functions_v2(data, f, g, h):
    """Version 2: Different multiplier, same composition."""
    # Same composition
    return __extracted_func_1(data, f, g, h, 3)


def partial_application_a(values, base_func, modifier):
    """Version A: Partial function application."""
    # Partial application
    return __extracted_func_7(values, base_func, modifier, "config1")


def partial_application_b(values, base_func, modifier):
    """Version B: Different config, same partial application."""
    # Same partial application pattern
    return __extracted_func_7(values, base_func, modifier, "config2")


def curry_functions_v1(data, operation, param1, param2):
    """Version 1: Currying pattern."""
    # Curried functions
    curried = lambda a: lambda b: lambda c: operation(a, b, c)

    # Apply currying
    return __extracted_func_3(data, param1, param2, curried, 2)


def curry_functions_v2(data, operation, param1, param2):
    """Version 2: Different multiplier, same currying."""
    # Same currying pattern
    curried = lambda a: lambda b: lambda c: operation(a, b, c)

    # Apply currying
    return __extracted_func_3(data, param1, param2, curried, 3)


def generator_with_lambda_a(data, predicate):
    """Version A: Generator with lambda."""
    # Generator expression with lambda
    return __extracted_func_5(data, predicate, 2)


def generator_with_lambda_b(data, predicate):
    """Version B: Different multiplier, same generator pattern."""
    # Same generator pattern
    return __extracted_func_5(data, predicate, 3)
