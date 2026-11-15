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
    base = price * 0.9
    if (customer_level > 5 and base > 100) or (customer_level > 10 and base > 50):
        final = base - 20
        print(f"Applied discount: {final}")
        return final
    return base


def calculate_discount_tier2(price, customer_level):
    """Tier 2: Different boolean expression, same structure."""
    base = price * 0.9
    if (customer_level > 3 and base > 200) or (customer_level > 8 and base > 75):
        final = base - 20
        print(f"Applied discount: {final}")
        return final
    return base


def transform_data_format_a(data, processor):
    """Format A: Nested method calls and indexing."""
    result = processor.normalize(data["values"]).upper().strip()
    validated = processor.validate(result)
    if validated:
        processor.store(result)
        return result
    return None


def transform_data_format_b(data, processor):
    """Format B: Different nested calls, same pattern."""
    result = processor.normalize(data["items"]).lower().strip()
    validated = processor.validate(result)
    if validated:
        processor.store(result)
        return result
    return None


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
