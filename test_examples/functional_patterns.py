"""
Test example: Lambda expressions and functional programming patterns.

Tests that the unifier handles lambdas, higher-order functions,
map/filter/reduce patterns, and functional composition.
"""


def apply_transformation_v1(data, multiplier):
    """Version 1: Lambda with captured variable."""
    # Lambda that captures multiplier
    transform = lambda x: x * multiplier + 10
    filtered = filter(lambda x: x > 0, data)
    result = list(map(transform, filtered))

    if len(result) > 0:
        return sorted(result, key=lambda x: x, reverse=True)
    return []


def apply_transformation_v2(data, multiplier):
    """Version 2: Different offset, same lambda pattern."""
    # Same pattern, different offset
    transform = lambda x: x * multiplier + 20
    filtered = filter(lambda x: x > 0, data)
    result = list(map(transform, filtered))

    if len(result) > 0:
        return sorted(result, key=lambda x: x, reverse=True)
    return []


def reduce_with_lambda_a(values, initial, combiner):
    """Version A: Reduce with lambda."""
    from functools import reduce

    # Complex reduce operation
    result = reduce(
        lambda acc, x: acc + (x ** 2 if x > 10 else x),
        values,
        initial
    )

    # Post-process with lambda
    normalized = list(map(lambda x: x / result if result != 0 else 0, values))

    return {"reduced": result, "normalized": normalized}


def reduce_with_lambda_b(values, initial, combiner):
    """Version B: Different threshold, same reduce pattern."""
    from functools import reduce

    # Same pattern, different threshold
    result = reduce(
        lambda acc, x: acc + (x ** 2 if x > 20 else x),
        values,
        initial
    )

    # Post-process with lambda
    normalized = list(map(lambda x: x / result if result != 0 else 0, values))

    return {"reduced": result, "normalized": normalized}


def chain_functional_ops_v1(data, filter_func, map_func):
    """Version 1: Chained functional operations."""
    # Functional pipeline
    step1 = filter(lambda x: x is not None and x > 0, data)
    step2 = map(lambda x: x * 2, step1)
    step3 = filter(filter_func, step2)
    step4 = map(map_func, step3)
    result = list(step4)

    # Final transformation
    if result:
        return sorted(result, key=lambda x: (x % 10, x))
    return []


def chain_functional_ops_v2(data, filter_func, map_func):
    """Version 2: Different multiplier, same pipeline."""
    # Same pipeline, different multiplier
    step1 = filter(lambda x: x is not None and x > 0, data)
    step2 = map(lambda x: x * 3, step1)
    step3 = filter(filter_func, step2)
    step4 = map(map_func, step3)
    result = list(step4)

    # Final transformation
    if result:
        return sorted(result, key=lambda x: (x % 10, x))
    return []


def higher_order_function_a(data, threshold):
    """Version A: Returns and uses functions."""
    # Create higher-order functions
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    # Use the functions
    validator = make_validator(5)
    transformer = make_transformer(2)

    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))

    return transformed


def higher_order_function_b(data, threshold):
    """Version B: Different limit, same higher-order pattern."""
    # Same pattern, different limit
    def make_validator(limit):
        return lambda x: x > limit and x < limit * 10

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    # Use the functions
    validator = make_validator(10)
    transformer = make_transformer(2)

    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))

    return transformed


def compose_functions_v1(data, f, g, h):
    """Version 1: Function composition."""
    # Compose functions
    compose = lambda x: h(g(f(x)))

    results = []
    for item in data:
        try:
            result = compose(item * 2)
            if result is not None:
                results.append(result)
        except Exception:
            continue

    return results


def compose_functions_v2(data, f, g, h):
    """Version 2: Different multiplier, same composition."""
    # Same composition
    compose = lambda x: h(g(f(x)))

    results = []
    for item in data:
        try:
            result = compose(item * 3)
            if result is not None:
                results.append(result)
        except Exception:
            continue

    return results


def partial_application_a(values, base_func, modifier):
    """Version A: Partial function application."""
    # Partial application
    apply_modifier = lambda x: base_func(x, modifier, "config1")

    # Use partially applied function
    processed = list(map(apply_modifier, values))
    filtered = list(filter(lambda x: x > 100, processed))

    return {
        "processed": processed,
        "filtered": filtered,
        "count": len(filtered)
    }


def partial_application_b(values, base_func, modifier):
    """Version B: Different config, same partial application."""
    # Same partial application pattern
    apply_modifier = lambda x: base_func(x, modifier, "config2")

    # Use partially applied function
    processed = list(map(apply_modifier, values))
    filtered = list(filter(lambda x: x > 100, processed))

    return {
        "processed": processed,
        "filtered": filtered,
        "count": len(filtered)
    }


def curry_functions_v1(data, operation, param1, param2):
    """Version 1: Currying pattern."""
    # Curried functions
    curried = lambda a: lambda b: lambda c: operation(a, b, c)

    # Apply currying
    stage1 = curried(param1)
    stage2 = stage1(param2)

    results = []
    for item in data:
        result = stage2(item * 2)
        if result > 0:
            results.append(result)

    return results


def curry_functions_v2(data, operation, param1, param2):
    """Version 2: Different multiplier, same currying."""
    # Same currying pattern
    curried = lambda a: lambda b: lambda c: operation(a, b, c)

    # Apply currying
    stage1 = curried(param1)
    stage2 = stage1(param2)

    results = []
    for item in data:
        result = stage2(item * 3)
        if result > 0:
            results.append(result)

    return results


def generator_with_lambda_a(data, predicate):
    """Version A: Generator with lambda."""
    # Generator expression with lambda
    transformed = (item * 2 for item in data if predicate(item))
    filtered = (x for x in transformed if x > 10)
    squared = (x ** 2 for x in filtered)

    # Consume generator
    result = list(squared)
    if result:
        return sorted(result, key=lambda x: -x)[:100]
    return []


def generator_with_lambda_b(data, predicate):
    """Version B: Different multiplier, same generator pattern."""
    # Same generator pattern
    transformed = (item * 3 for item in data if predicate(item))
    filtered = (x for x in transformed if x > 10)
    squared = (x ** 2 for x in filtered)

    # Consume generator
    result = list(squared)
    if result:
        return sorted(result, key=lambda x: -x)[:100]
    return []
