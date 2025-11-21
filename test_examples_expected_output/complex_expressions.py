"""
Test example: Complex expressions as parameters.

Tests that the unifier can properly parameterize complex sub-expressions
including nested calls, arithmetic, boolean logic, and method chains.
"""


def process_user_score_v1(user, threshold):
    """Version 1: Complex expression with nested arithmetic."""
    if user.get_score() * 2 + 10 > threshold:
        user.set_status("premium")
        user.update_timestamp()
        return user.get_score() * 2 + 10
    return 0


def process_user_score_v2(user, threshold):
    """Version 2: Different complex expression, same structure."""
    if user.get_score() * 3 - 5 > threshold:
        user.set_status("premium")
        user.update_timestamp()
        return user.get_score() * 3 - 5
    return 0


def calculate_discount_tier1(price, customer_level):
    """Tier 1: Complex boolean expression."""
    return __extracted_func_0(5, 100, 10, 50, customer_level, price)


def calculate_discount_tier2(price, customer_level):
    """Tier 2: Different boolean expression, same structure."""
    return __extracted_func_0(3, 200, 8, 75, customer_level, price)


def transform_data_format_a(data, processor):
    """Format A: Nested method calls and indexing."""
    result = processor.normalize(data["values"]).upper().strip()
    return __extracted_func_1(processor, result)


def transform_data_format_b(data, processor):
    """Format B: Different nested calls, same pattern."""
    result = processor.normalize(data["items"]).lower().strip()
    return __extracted_func_1(processor, result)


def compute_metrics_slow(dataset, multiplier, offset):
    """Slow computation: Complex arithmetic with multiple operations."""
    total = sum([x * multiplier + offset for x in dataset])
    average = total / len(dataset) if dataset else 0
    variance = sum([(x * multiplier + offset - average) ** 2 for x in dataset])
    return {"total": total, "avg": average, "var": variance}


def compute_metrics_fast(dataset, multiplier, offset):
    """Fast computation: Different multiplier/offset, same algorithm."""
    total = sum([x * multiplier - offset for x in dataset])
    average = total / len(dataset) if dataset else 0
    variance = sum([(x * multiplier - offset - average) ** 2 for x in dataset])
    return {"total": total, "avg": average, "var": variance}


def __extracted_func_0(__param_0, __param_1, __param_2, __param_3, customer_level, price):
    base = price * 0.9
    if customer_level > __param_0 and base > __param_1 or (customer_level > __param_2 and base > __param_3):
        final = base - 20
        print(f'Applied discount: {final}')
        return final
    return base


def __extracted_func_1(processor, result):
    validated = processor.validate(result)
    if validated:
        processor.store(result)
        return result
    return None




