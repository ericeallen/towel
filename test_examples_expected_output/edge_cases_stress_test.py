"""
Test example: Edge cases and stress tests.

Combines multiple challenging patterns to really stress test the unifier:
- Deep nesting with multiple levels
- Very long expressions
- Multiple parameters of different types
- Complex control flow
- Edge cases that might break naive implementations
"""


def __extracted_func_7(e, errors, fallback, i, logger, results):
    logger.error(f'Unexpected error at index {i}: {e}')
    errors['other'].append((i, str(e)))
    results.append(fallback.get_generic_default())


def __extracted_func_6(e, errors, fallback, i, logger, results):
    logger.error(f'RuntimeError at index {i}: {e}')
    errors['runtime'].append((i, str(e)))
    results.append(fallback.get_runtime_default())


def __extracted_func_5(e, errors, fallback, i, logger, results):
    logger.error(f'TypeError at index {i}: {e}')
    errors['type'].append((i, str(e)))
    results.append(fallback.get_type_default())


def __extracted_func_4(e, errors, fallback, i, logger, results):
    logger.error(f'ValueError at index {i}: {e}')
    errors['value'].append((i, str(e)))
    results.append(fallback.get_value_default())


def __extracted_func_3(handlers, transformed):
    if transformed > 100:
        handlers['high'].handle(transformed)
    elif transformed > 50:
        handlers['medium'].handle(transformed)
    else:
        handlers['low'].handle(transformed)


def __extracted_func_2(__param_0, action_result, current_state, event, history, old_state, outputs):
    outputs.append(action_result * __param_0)
    history.append({'from': old_state, 'to': current_state, 'event': event['type'], 'result': action_result})


def __extracted_func_1(computed, inner_item, logger, metrics, normalized, results, validated):
    if validated:
        logger.debug(f'Validated: {normalized}')
        metrics.record('validated', normalized)
        results.append({'original': inner_item['value'], 'computed': computed, 'normalized': normalized})


def __extracted_func_0(__param_0, data, filters, mappers, _towel_owner):
    list_comp = [x * __param_0 for x in data if filters['positive'](x)]
    dict_comp = {k: v * __param_0 for k, v in enumerate(list_comp) if v > 10}
    set_comp = {v for v in dict_comp.values() if v < 1000}
    # Nested comprehension
    nested = [[mappers['inner'](y) for y in row if y is not None] for row in [list_comp[i:i + 5] for i in range(0, len(list_comp), 5)] if len(row) > 0]
    return {'list': list_comp, 'dict': dict_comp, 'set': set_comp, 'nested': nested}


def deeply_nested_computation_v1(data, config, cache, logger, metrics):
    """Version 1: Deep nesting with many variables."""
    results = []

    for outer_item in data:
        if outer_item.get("enabled"):
            for middle_item in outer_item.get("children", []):
                if middle_item.get("status") == "active":
                    for inner_item in middle_item.get("values", []):
                        # This deeply nested block should be extractable
                        computed = (
                            inner_item["value"] * config["multiplier"] + config["offset"]
                        ) ** config["power"]

                        if computed > config["threshold"]:
                            normalized = computed / config["normalizer"]
                            validated = cache.check(normalized)

                            __extracted_func_1(computed, inner_item, logger, metrics, normalized, results, validated)

    return results


def deeply_nested_computation_v2(data, config, cache, logger, metrics):
    """Version 2: Same nesting, different multiplier."""
    results = []

    for outer_item in data:
        if outer_item.get("enabled"):
            for middle_item in outer_item.get("children", []):
                if middle_item.get("status") == "active":
                    for inner_item in middle_item.get("values", []):
                        # Same structure, different multiplier
                        computed = (
                            inner_item["value"] * config["factor"] + config["offset"]
                        ) ** config["power"]

                        if computed > config["threshold"]:
                            normalized = computed / config["normalizer"]
                            validated = cache.check(normalized)

                            __extracted_func_1(computed, inner_item, logger, metrics, normalized, results, validated)

    return results


def many_parameters_v1(a, b, c, d, e, f, g, h):
    """Version 1: Many parameters, complex expression."""
    # Very complex expression using many variables
    step1 = (a + b) * (c - d) + (e / f if f != 0 else 0) ** (g % 5)
    step2 = step1 + h
    step3 = step2 * 2

    if step3 > 100:
        result = step3 / (a + 1)
        formatted = f"{result:.2f}"
        return formatted
    return "0.00"


def many_parameters_v2(a, b, c, d, e, f, g, h):
    """Version 2: Different multiplier in step3."""
    # Same complex expression, different multiplier
    step1 = (a + b) * (c - d) + (e / f if f != 0 else 0) ** (g % 5)
    step2 = step1 + h
    step3 = step2 * 3

    if step3 > 100:
        result = step3 / (a + 1)
        formatted = f"{result:.2f}"
        return formatted
    return "0.00"


def complex_control_flow_a(data, validators, transformers, handlers):
    """Version A: Complex control flow with multiple branches."""
    for item in data:
        if item.get("type") == "A":
            if validators["A"].validate(item):
                transformed = transformers["A"].transform(item["value"])
                __extracted_func_3(handlers, transformed)
        elif item.get("type") == "B":
            if validators["B"].validate(item):
                transformed = transformers["B"].transform(item["data"])
                __extracted_func_3(handlers, transformed)


def complex_control_flow_b(data, validators, transformers, handlers):
    """Version B: Different keys, same control flow."""
    for item in data:
        if item.get("category") == "A":
            if validators["A"].validate(item):
                transformed = transformers["A"].transform(item["amount"])
                __extracted_func_3(handlers, transformed)
        elif item.get("category") == "B":
            if validators["B"].validate(item):
                transformed = transformers["B"].transform(item["total"])
                __extracted_func_3(handlers, transformed)


def mixed_comprehensions_v1(data, filters, mappers):
    """Version 1: Multiple comprehension types."""
    # Mix of comprehensions
    _towel_arguments = [(mappers, filters, data)]
    del data
    del filters
    del mappers
    return __extracted_func_0(2, _towel_arguments[0][2], _towel_arguments[0][1], _towel_arguments[0][0], _towel_arguments.pop())


def mixed_comprehensions_v2(data, filters, mappers):
    """Version 2: Different multiplier, same comprehensions."""
    # Same structure, different multiplier
    _towel_arguments = [(mappers, filters, data)]
    del data
    del filters
    del mappers
    return __extracted_func_0(3, _towel_arguments[0][2], _towel_arguments[0][1], _towel_arguments[0][0], _towel_arguments.pop())


def exception_heavy_processing_a(items, processor, logger, fallback):
    """Version A: Multiple exception types and handlers."""
    results = []
    errors = {"value": [], "type": [], "runtime": [], "other": []}

    for i, item in enumerate(items):
        try:
            # Multiple exception-raising operations
            validated = processor.validate(item, strict=True)
            parsed = processor.parse(validated)
            transformed = processor.transform(parsed, mode="strict")
            normalized = processor.normalize(transformed)

            results.append(normalized)

        except ValueError as e:
            __extracted_func_4(e, errors, fallback, i, logger, results)

        except TypeError as e:
            __extracted_func_5(e, errors, fallback, i, logger, results)

        except RuntimeError as e:
            __extracted_func_6(e, errors, fallback, i, logger, results)

        except Exception as e:
            __extracted_func_7(e, errors, fallback, i, logger, results)

    return {"results": results, "errors": errors}


def exception_heavy_processing_b(items, processor, logger, fallback):
    """Version B: Different processing mode, same exception handling."""
    results = []
    errors = {"value": [], "type": [], "runtime": [], "other": []}

    for i, item in enumerate(items):
        try:
            # Same structure, different mode
            validated = processor.validate(item, strict=True)
            parsed = processor.parse(validated)
            transformed = processor.transform(parsed, mode="lenient")
            normalized = processor.normalize(transformed)

            results.append(normalized)

        except ValueError as e:
            __extracted_func_4(e, errors, fallback, i, logger, results)

        except TypeError as e:
            __extracted_func_5(e, errors, fallback, i, logger, results)

        except RuntimeError as e:
            __extracted_func_6(e, errors, fallback, i, logger, results)

        except Exception as e:
            __extracted_func_7(e, errors, fallback, i, logger, results)

    return {"results": results, "errors": errors}


def state_machine_pattern_v1(events, states, transitions, handlers):
    """Version 1: State machine implementation."""
    current_state = "initial"
    history = []
    outputs = []

    for event in events:
        # State machine logic
        if current_state in states:
            valid_transitions = transitions.get(current_state, [])

            if event["type"] in [t["event"] for t in valid_transitions]:
                for transition in valid_transitions:
                    if transition["event"] == event["type"]:
                        # Execute transition
                        if transition.get("guard", lambda: True)():
                            old_state = current_state
                            current_state = transition["target"]

                            # Execute actions
                            action_result = handlers[transition["action"]](event, current_state)
                            __extracted_func_2(2, action_result, current_state, event, history, old_state, outputs)

    return {"final_state": current_state, "history": history, "outputs": outputs}


def state_machine_pattern_v2(events, states, transitions, handlers):
    """Version 2: Different multiplier, same state machine."""
    current_state = "initial"
    history = []
    outputs = []

    for event in events:
        # Same state machine logic
        if current_state in states:
            valid_transitions = transitions.get(current_state, [])

            if event["type"] in [t["event"] for t in valid_transitions]:
                for transition in valid_transitions:
                    if transition["event"] == event["type"]:
                        # Execute transition
                        if transition.get("guard", lambda: True)():
                            old_state = current_state
                            current_state = transition["target"]

                            # Different multiplier
                            action_result = handlers[transition["action"]](event, current_state)
                            __extracted_func_2(3, action_result, current_state, event, history, old_state, outputs)

    return {"final_state": current_state, "history": history, "outputs": outputs}
