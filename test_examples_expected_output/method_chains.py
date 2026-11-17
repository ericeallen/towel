"""
Test example: Method chains and object-oriented patterns.

Tests that the unifier handles method chaining, attribute access,
and object-oriented code patterns correctly.
"""


def __extracted_func_6(model, validator):
    if validator.is_valid(model):
        model.save()
        model.notify_observers()
        return True
    return False


def __extracted_func_5(response, validator, transformer, _param_0, _param_1):
    data = response.json().get(_param_0, {}).get(_param_1, [])
    cleaned = [item.strip().lower() for item in data]
    validated = [validator.check(item) for item in cleaned]
    result = transformer.process(validated).filter(lambda x: x is not None).to_list()

    if len(result) > 0:
        transformer.commit()
        return result
    return []


def __extracted_func_4(data, serializer, cache, _param_0):
    response = serializer.create_response()
    response.set_data(data)
    response.set_status(200)
    response.add_header("Content-Type", "application/json")
    response.add_header("Cache-Control", _param_0)
    # Cache the response
    cache_key = serializer.generate_key(data)
    cache.set(cache_key, response.to_dict())

    return response.build()


def __extracted_func_3(entity, transformer, validator, _param_0):
    entity.set_field(_param_0, True)
    entity.increment_version()
    transformed_data = transformer.apply(entity.get_data())
    entity.set_data(transformed_data)
    # Validate
    errors = validator.validate_entity(entity)
    if not errors:
        entity.mark_clean()
        entity.save_to_store()
        return entity
    return None


def __extracted_func_2(results, aggregator, formatter, _param_0, _param_1, _param_2, _param_3):
    total = aggregator.sum([_param_0(r) for r in results])
    average = aggregator.average([_param_0(r) for r in results])
    maximum = aggregator.max([_param_0(r) for r in results])

    formatted = (
        formatter.create_summary()
        .add_metric(_param_1, total)
        .add_metric(_param_2, average)
        .add_metric(_param_3, maximum)
        .finalize()
    )

    return formatted


def __extracted_func_1(stream, parser, handler, _param_0):
    processed = (
        stream.filter(lambda x: x.is_valid())
        .map(parser.parse)
        .filter(lambda x: x is not None)
        .take(_param_0)
    )

    for item in processed:
        handler.process(item)
        handler.update_metrics(item.get_size())
        if handler.should_commit():
            handler.commit()

    return handler.get_statistics()


def __extracted_func_0(db, mapper, _param_0):
    results = (
        db.table("users")
        .where("age", ">", _param_0)
        .where("status", "=", "active")
        .order_by("created_at", "desc")
        .limit(100)
        .get()
    )
    # Map results
    mapped = [mapper.to_dto(row) for row in results]
    validated = [item for item in mapped if item.is_valid()]

    return validated


def process_api_response_v1(response, validator, transformer):
    """Version 1: Method chaining on API response."""
    # Complex method chain
    return __extracted_func_5(response, validator, transformer, "data", "items")


def process_api_response_v2(response, validator, transformer):
    """Version 2: Different key path, same chaining pattern."""
    # Different keys, same chain
    return __extracted_func_5(response, validator, transformer, "payload", "records")


def update_model_fields_a(model, updates, validator):
    """Version A: Fluent interface pattern."""
    # Fluent method calls
    model.set_name(updates["name"]).set_description(updates["description"]).set_status(
        "active"
    ).validate()

    return __extracted_func_6(model, validator)


def update_model_fields_b(model, updates, validator):
    """Version B: Different keys, same fluent pattern."""
    # Same fluent pattern
    model.set_title(updates["title"]).set_content(updates["content"]).set_status(
        "active"
    ).validate()

    return __extracted_func_6(model, validator)


def query_database_v1(db, filters, mapper):
    """Version 1: Database query builder pattern."""
    # Method chaining query
    return __extracted_func_0(db, mapper, 18)


def query_database_v2(db, filters, mapper):
    """Version 2: Different age threshold, same pattern."""
    # Same query pattern, different threshold
    return __extracted_func_0(db, mapper, 21)


def process_stream_v1(stream, parser, handler):
    """Version 1: Stream processing with method calls."""
    # Stream operations
    return __extracted_func_1(stream, parser, handler, 1000)


def process_stream_v2(stream, parser, handler):
    """Version 2: Different take limit, same pattern."""
    # Same stream pattern, different limit
    return __extracted_func_1(stream, parser, handler, 5000)


def build_response_a(data, serializer, cache):
    """Version A: Response builder with multiple method calls."""
    # Building response
    return __extracted_func_4(data, serializer, cache, "max-age=3600")


def build_response_b(data, serializer, cache):
    """Version B: Different cache duration, same pattern."""
    # Same building pattern
    return __extracted_func_4(data, serializer, cache, "max-age=7200")


def transform_entity_v1(entity, transformer, validator):
    """Version 1: Entity transformation with validation."""
    # Transform entity
    return __extracted_func_3(entity, transformer, validator, "processed")


def transform_entity_v2(entity, transformer, validator):
    """Version 2: Different field name, same pattern."""
    # Same transformation pattern
    return __extracted_func_3(entity, transformer, validator, "completed")


def aggregate_results_a(results, aggregator, formatter):
    """Version A: Aggregation with multiple method calls."""
    # Aggregate and format
    return __extracted_func_2(results, aggregator, formatter, lambda r: r.get_value(), "total", "average", "maximum")


def aggregate_results_b(results, aggregator, formatter):
    """Version B: Different metric names, same pattern."""
    # Same aggregation pattern
    return __extracted_func_2(results, aggregator, formatter, lambda r: r.get_amount(), "sum", "mean", "max")
