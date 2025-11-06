"""
Test example: Method chains and object-oriented patterns.

Tests that the unifier handles method chaining, attribute access,
and object-oriented code patterns correctly.
"""


def process_api_response_v1(response, validator, transformer):
    """Version 1: Method chaining on API response."""
    # Complex method chain
    return __extracted_func_4('data', 'items', response, transformer, validator)


def process_api_response_v2(response, validator, transformer):
    """Version 2: Different key path, same chaining pattern."""
    # Different keys, same chain
    return __extracted_func_4('payload', 'records', response, transformer, validator)


def update_model_fields_a(model, updates, validator):
    """Version A: Fluent interface pattern."""
    # Fluent method calls
    return __extracted_func_5(model.set_name, 'name', lambda *args, **kwargs: model.set_name(updates['name']).set_description(*args, **kwargs), 'description', model, updates, validator)


def update_model_fields_b(model, updates, validator):
    """Version B: Different keys, same fluent pattern."""
    # Same fluent pattern
    return __extracted_func_5(model.set_title, 'title', lambda *args, **kwargs: model.set_title(updates['title']).set_content(*args, **kwargs), 'content', model, updates, validator)


def query_database_v1(db, filters, mapper):
    """Version 1: Database query builder pattern."""
    # Method chaining query
    return extracted_func(18, db, mapper)


def query_database_v2(db, filters, mapper):
    """Version 2: Different age threshold, same pattern."""
    # Same query pattern, different threshold
    return extracted_func(21, db, mapper)


def process_stream_v1(stream, parser, handler):
    """Version 1: Stream processing with method calls."""
    # Stream operations
    return __extracted_func_1(1000, handler, parser, stream)


def process_stream_v2(stream, parser, handler):
    """Version 2: Different take limit, same pattern."""
    # Same stream pattern, different limit
    return __extracted_func_1(5000, handler, parser, stream)


def build_response_a(data, serializer, cache):
    """Version A: Response builder with multiple method calls."""
    # Building response
    return __extracted_func_3('max-age=3600', cache, data, serializer)


def build_response_b(data, serializer, cache):
    """Version B: Different cache duration, same pattern."""
    # Same building pattern
    return __extracted_func_3('max-age=7200', cache, data, serializer)


def transform_entity_v1(entity, transformer, validator):
    """Version 1: Entity transformation with validation."""
    # Transform entity
    return __extracted_func_2('processed', entity, transformer, validator)


def transform_entity_v2(entity, transformer, validator):
    """Version 2: Different field name, same pattern."""
    # Same transformation pattern
    return __extracted_func_2('completed', entity, transformer, validator)


def aggregate_results_a(results, aggregator, formatter):
    """Version A: Aggregation with multiple method calls."""
    # Aggregate and format
    total = aggregator.sum([r.get_value() for r in results])
    average = aggregator.average([r.get_value() for r in results])
    maximum = aggregator.max([r.get_value() for r in results])

    return __extracted_func_6('total', 'average', 'maximum', average, formatter, maximum, total)


def aggregate_results_b(results, aggregator, formatter):
    """Version B: Different metric names, same pattern."""
    # Same aggregation pattern
    total = aggregator.sum([r.get_amount() for r in results])
    average = aggregator.average([r.get_amount() for r in results])
    maximum = aggregator.max([r.get_amount() for r in results])

    return __extracted_func_6('sum', 'mean', 'max', average, formatter, maximum, total)


def extracted_func(__param_0, db, mapper):
    results = db.table('users').where('age', '>', __param_0).where('status', '=', 'active').order_by('created_at', 'desc').limit(100).get()
    mapped = [mapper.to_dto(row) for row in results]
    validated = [item for item in mapped if item.is_valid()]
    return validated


def __extracted_func_1(__param_0, handler, parser, stream):
    processed = stream.filter(lambda x: x.is_valid()).map(parser.parse).filter(lambda x: x is not None).take(__param_0)
    for item in processed:
        handler.process(item)
        handler.update_metrics(item.get_size())
        if handler.should_commit():
            handler.commit()
    return handler.get_statistics()


def __extracted_func_2(__param_0, entity, transformer, validator):
    entity.set_field(__param_0, True)
    entity.increment_version()
    transformed_data = transformer.apply(entity.get_data())
    entity.set_data(transformed_data)
    errors = validator.validate_entity(entity)
    if not errors:
        entity.mark_clean()
        entity.save_to_store()
        return entity
    return None


def __extracted_func_3(__param_0, cache, data, serializer):
    response = serializer.create_response()
    response.set_data(data)
    response.set_status(200)
    response.add_header('Content-Type', 'application/json')
    response.add_header('Cache-Control', __param_0)
    cache_key = serializer.generate_key(data)
    cache.set(cache_key, response.to_dict())
    return response.build()


def __extracted_func_4(__param_0, __param_1, response, transformer, validator):
    data = response.json().get(__param_0, {}).get(__param_1, [])
    cleaned = [item.strip().lower() for item in data]
    validated = [validator.check(item) for item in cleaned]
    result = transformer.process(validated).filter(lambda x: x is not None).to_list()
    if len(result) > 0:
        transformer.commit()
        return result
    return []


def __extracted_func_5(__param_0, __param_1, __param_2, __param_3, model, updates, validator):
    __param_2(updates[__param_3]).set_status('active').validate()
    if validator.is_valid(model):
        model.save()
        model.notify_observers()
        return True
    return False


def __extracted_func_6(__param_0, __param_1, __param_2, average, formatter, maximum, total):
    formatted = formatter.create_summary().add_metric(__param_0, total).add_metric(__param_1, average).add_metric(__param_2, maximum).finalize()
    return formatted














