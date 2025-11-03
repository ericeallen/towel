"""
Test example: Edge cases and stress tests.

Combines multiple challenging patterns to really stress test the unifier:
- Deep nesting with multiple levels
- Very long expressions
- Multiple parameters of different types
- Complex control flow
- Edge cases that might break naive implementations
"""


def deeply_nested_computation_v1(data, config, cache, logger, metrics):
    """Version 1: Deep nesting with many variables."""
    return __extracted_func_268('multiplier', cache, config, data, logger, metrics)


def deeply_nested_computation_v2(data, config, cache, logger, metrics):
    """Version 2: Same nesting, different multiplier."""
    return __extracted_func_268('factor', cache, config, data, logger, metrics)


def many_parameters_v1(a, b, c, d, e, f, g, h):
    """Version 1: Many parameters, complex expression."""
    # Very complex expression using many variables
    return __extracted_func_346(2, a, b, c, d, e, f, g, h)


def many_parameters_v2(a, b, c, d, e, f, g, h):
    """Version 2: Different multiplier in step3."""
    # Same complex expression, different multiplier
    return __extracted_func_346(3, a, b, c, d, e, f, g, h)


def complex_control_flow_a(data, validators, transformers, handlers):
    """Version A: Complex control flow with multiple branches."""
    __extracted_func_312('type', 'value', 'data', data, handlers, transformers, validators)


def complex_control_flow_b(data, validators, transformers, handlers):
    """Version B: Different keys, same control flow."""
    __extracted_func_312('category', 'amount', 'total', data, handlers, transformers, validators)


def mixed_comprehensions_v1(data, filters, mappers):
    """Version 1: Multiple comprehension types."""
    # Mix of comprehensions
    return __extracted_func_337(2, data, filters, mappers)


def mixed_comprehensions_v2(data, filters, mappers):
    """Version 2: Different multiplier, same comprehensions."""
    # Same structure, different multiplier
    return __extracted_func_337(3, data, filters, mappers)


def exception_heavy_processing_a(items, processor, logger, fallback):
    """Version A: Multiple exception types and handlers."""
    return __extracted_func_209('strict', fallback, items, logger, processor)


def exception_heavy_processing_b(items, processor, logger, fallback):
    """Version B: Different processing mode, same exception handling."""
    return __extracted_func_209('lenient', fallback, items, logger, processor)


def state_machine_pattern_v1(events, states, transitions, handlers):
    """Version 1: State machine implementation."""
    return __extracted_func_264(2, events, handlers, states, transitions)


def state_machine_pattern_v2(events, states, transitions, handlers):
    """Version 2: Different multiplier, same state machine."""
    return __extracted_func_264(3, events, handlers, states, transitions)


def __extracted_func_209(__param_0, fallback, items, logger, processor):
    results = []
    errors = {'value': [], 'type': [], 'runtime': [], 'other': []}
    for i, item in enumerate(items):
        try:
            validated = processor.validate(item, strict=True)
            parsed = processor.parse(validated)
            transformed = processor.transform(parsed, mode=__param_0)
            normalized = processor.normalize(transformed)
            results.append(normalized)
        except ValueError as e:
            logger.error(f'ValueError at index {i}: {e}')
            errors['value'].append((i, str(e)))
            results.append(fallback.get_value_default())
        except TypeError as e:
            logger.error(f'TypeError at index {i}: {e}')
            errors['type'].append((i, str(e)))
            results.append(fallback.get_type_default())
        except RuntimeError as e:
            logger.error(f'RuntimeError at index {i}: {e}')
            errors['runtime'].append((i, str(e)))
            results.append(fallback.get_runtime_default())
        except Exception as e:
            logger.error(f'Unexpected error at index {i}: {e}')
            errors['other'].append((i, str(e)))
            results.append(fallback.get_generic_default())
    return {'results': results, 'errors': errors}


def __extracted_func_264(__param_0, events, handlers, states, transitions):
    current_state = 'initial'
    history = []
    outputs = []
    for event in events:
        if current_state in states:
            valid_transitions = transitions.get(current_state, [])
            if event['type'] in [t['event'] for t in valid_transitions]:
                for transition in valid_transitions:
                    if transition['event'] == event['type']:
                        if transition.get('guard', lambda: True)():
                            old_state = current_state
                            current_state = transition['target']
                            action_result = handlers[transition['action']](event, current_state)
                            outputs.append(action_result * __param_0)
                            history.append({'from': old_state, 'to': current_state, 'event': event['type'], 'result': action_result})
    return {'final_state': current_state, 'history': history, 'outputs': outputs}


def __extracted_func_268(__param_0, cache, config, data, logger, metrics):
    results = []
    for outer_item in data:
        if outer_item.get('enabled'):
            for middle_item in outer_item.get('children', []):
                if middle_item.get('status') == 'active':
                    for inner_item in middle_item.get('values', []):
                        computed = (inner_item['value'] * config[__param_0] + config['offset']) ** config['power']
                        if computed > config['threshold']:
                            normalized = computed / config['normalizer']
                            validated = cache.check(normalized)
                            if validated:
                                logger.debug(f'Validated: {normalized}')
                                metrics.record('validated', normalized)
                                results.append({'original': inner_item['value'], 'computed': computed, 'normalized': normalized})
    return results


def __extracted_func_312(__param_0, __param_1, __param_2, data, handlers, transformers, validators):
    for item in data:
        if item.get(__param_0) == 'A':
            if validators['A'].validate(item):
                transformed = transformers['A'].transform(item[__param_1])
                if transformed > 100:
                    handlers['high'].handle(transformed)
                elif transformed > 50:
                    handlers['medium'].handle(transformed)
                else:
                    handlers['low'].handle(transformed)
        elif item.get(__param_0) == 'B':
            if validators['B'].validate(item):
                transformed = transformers['B'].transform(item[__param_2])
                if transformed > 100:
                    handlers['high'].handle(transformed)
                elif transformed > 50:
                    handlers['medium'].handle(transformed)
                else:
                    handlers['low'].handle(transformed)


def __extracted_func_337(__param_0, data, filters, mappers):
    list_comp = [x * __param_0 for x in data if filters['positive'](x)]
    dict_comp = {k: v * __param_0 for k, v in enumerate(list_comp) if v > 10}
    set_comp = {v for v in dict_comp.values() if v < 1000}
    nested = [[mappers['inner'](y) for y in row if y is not None] for row in [list_comp[i:i + 5] for i in range(0, len(list_comp), 5)] if len(row) > 0]
    return {'list': list_comp, 'dict': dict_comp, 'set': set_comp, 'nested': nested}


def __extracted_func_346(__param_0, a, b, c, d, e, f, g, h):
    step1 = (a + b) * (c - d) + (e / f if f != 0 else 0) ** (g % 5)
    step2 = step1 + h
    step3 = step2 * __param_0
    if step3 > 100:
        result = step3 / (a + 1)
        formatted = f'{result:.2f}'
        return formatted
    return '0.00'












