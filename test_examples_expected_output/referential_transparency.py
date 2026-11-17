"""
Test example: Referential transparency and variable semantics.

Tests that extracted functions maintain the correct meaning of variables
and don't accidentally capture or modify variables incorrectly.
"""


def __extracted_func_5(accumulator, multiplier, value):
    processed = value * multiplier
    validated = processed > 0
    if validated:
        accumulator.append(processed)
    return processed


def __extracted_func_4(__param_0, counter, items):
    for item in items:
        # This block modifies mutable state
        # Same pattern of mutation
        counter['total'] += __param_0(item)
        counter['processed'] += 1
        result = counter['total'] / counter['processed']
        print(f'Current average: {result}')
    return counter


def __extracted_func_3(data, outer_var, processor):
    inner_var = 50
    for item in data:
        # This block uses both outer_var and inner_var
        step1 = item + outer_var
        step2 = step1 - inner_var
        step3 = step2 * 2  # Correct: uses step2
        processor.add(step3)


def __extracted_func_2(__param_0, cache, data, metrics):
    for key, value in data.items():
        # Modifies both cache and metrics
        # Same pattern, different multiplier
        processed = value * __param_0
        cache[key] = processed
        metrics['count'] += 1
        metrics['total'] += processed
        if processed > 100:
            metrics['high_value'] += 1
    return (cache, metrics)


def __extracted_func_1(__param_0, data, logger):
    total = 0
    for value in data:
        # Block with side effects
        # Different normalization, same structure
        normalized = __param_0(value)
        total += len(normalized)
        logger.info(f'Processed: {normalized}')
        logger.debug(f'Total so far: {total}')
    return total


def __extracted_func_0(__param_0, data, validator):
    for item in data:
        # Block with early returns
        # Same structure, different processing
        cleaned = item.strip()
        if not cleaned:
            return None
        validated = validator.check(cleaned)
        if not validated:
            return None
        processed = __param_0(cleaned)
        return processed
    return 'default'


def update_mutable_state_v1(items, counter):
    """Version 1: Modifies mutable state within block."""
    counter = {"total": 0, "processed": 0}

    return __extracted_func_4(lambda item: item, counter, items)


def update_mutable_state_v2(items, counter):
    """Version 2: Same mutable modifications, different initialization."""
    counter = {"total": 0, "processed": 0}

    return __extracted_func_4(lambda item: item * 2, counter, items)


def process_with_side_effects_a(data, logger):
    """Version A: Side effects in duplicate block."""
    return __extracted_func_1(lambda value: value.strip().lower(), data, logger)


def process_with_side_effects_b(data, logger):
    """Version B: Different normalization, same logging pattern."""
    return __extracted_func_1(lambda value: value.strip().upper(), data, logger)


def accumulate_with_closure_v1(items, accumulator):
    """Version 1: Creates closure over accumulator."""
    accumulator = []

    def add_processed(value, multiplier):
        # Closure captures 'accumulator'
        return __extracted_func_5(accumulator, multiplier, value)

    results = [add_processed(item, 2) for item in items]
    return accumulator, results


def accumulate_with_closure_v2(items, accumulator):
    """Version 2: Different multiplier, same closure pattern."""
    accumulator = []

    def add_processed(value, multiplier):
        # Same closure pattern
        return __extracted_func_5(accumulator, multiplier, value)

    results = [add_processed(item, 3) for item in items]
    return accumulator, results


def transform_with_early_return_a(data, validator):
    """Version A: Early returns in duplicate block."""
    return __extracted_func_0(lambda cleaned: cleaned.upper(), data, validator)


def transform_with_early_return_b(data, validator):
    """Version B: Same early return pattern, different processing."""
    return __extracted_func_0(lambda cleaned: cleaned.lower(), data, validator)


def nested_scope_capture_v1(outer_data, processor):
    """
    Version 1: Nested scopes with variable capture.

    NOTE: v2 contains an unbound variable reference (step3 = step3 * 2).
    This makes the code invalid and prevents refactoring because:
    - The difference is in expressions using step2 vs step3
    - These variables are local to the nested inner_process function
    - They don't exist at the outer scope where extraction would occur
    - Cannot pass undefined variables as arguments to extracted function

    See nested_scope_capture_valid_v1/v2 below for a corrected version
    that CAN be refactored.
    """
    outer_var = 100

    def inner_process(data):
        # Captures outer_var
        __extracted_func_3(data, outer_var, processor)

    inner_process(outer_data)
    return processor.get_results()


def nested_scope_capture_v2(outer_data, processor):
    """
    Version 2: Different outer_var, same nesting pattern.

    WARNING: Contains unbound variable reference on line: step3 = step3 * 2
    This is invalid Python code (NameError at runtime).
    The refactoring tool correctly refuses to refactor this.
    """
    outer_var = 200

    def inner_process(data):
        # Captures different outer_var
        inner_var = 50
        for item in data:
            # Same structure, different outer_var value
            step1 = item + outer_var
            step2 = step1 - inner_var
            step3 = step3 * 2  # BUG: Unbound variable! Should be: step3 = step2 * 2
            processor.add(step3)

    inner_process(outer_data)
    return processor.get_results()


def nested_scope_capture_valid_v1(outer_data, processor):
    """
    Corrected version 1: No unbound variables.

    These functions CAN be refactored because:
    - The only difference is the constant (100 vs 200)
    - No nested-scope variables are referenced in differing expressions
    - All differences can be parameterized at the outer scope level
    """
    outer_var = 100

    def inner_process(data):
        # Captures outer_var
        __extracted_func_3(data, outer_var, processor)

    inner_process(outer_data)
    return processor.get_results()


def nested_scope_capture_valid_v2(outer_data, processor):
    """
    Corrected version 2: No unbound variables.

    This version can be successfully refactored with v1 above.
    The extracted function will accept outer_var as a parameter.
    """
    outer_var = 200

    def inner_process(data):
        # Captures different outer_var
        __extracted_func_3(data, outer_var, processor)

    inner_process(outer_data)
    return processor.get_results()


def modify_external_state_a(data, cache, metrics):
    """Version A: Modifies multiple external objects."""
    return __extracted_func_2(2, cache, data, metrics)


def modify_external_state_b(data, cache, metrics):
    """Version B: Different multiplier, same modification pattern."""
    return __extracted_func_2(3, cache, data, metrics)
