"""
Test example: Method chains and object-oriented patterns.

Tests that the unifier handles method chaining, attribute access,
and object-oriented code patterns correctly.
"""


def process_api_response_v1(response, validator, transformer):
    """Version 1: Method chaining on API response."""
    # Complex method chain
    data = response.json().get("data", {}).get("items", [])
    cleaned = [item.strip().lower() for item in data]
    validated = [validator.check(item) for item in cleaned]
    result = transformer.process(validated).filter(lambda x: x is not None).to_list()

    if len(result) > 0:
        transformer.commit()
        return result
    return []


def process_api_response_v2(response, validator, transformer):
    """Version 2: Different key path, same chaining pattern."""
    # Different keys, same chain
    data = response.json().get("payload", {}).get("records", [])
    cleaned = [item.strip().lower() for item in data]
    validated = [validator.check(item) for item in cleaned]
    result = transformer.process(validated).filter(lambda x: x is not None).to_list()

    if len(result) > 0:
        transformer.commit()
        return result
    return []


def update_model_fields_a(model, updates, validator):
    """Version A: Fluent interface pattern."""
    # Fluent method calls
    model.set_name(updates["name"])\
         .set_description(updates["description"])\
         .set_status("active")\
         .validate()

    if validator.is_valid(model):
        model.save()
        model.notify_observers()
        return True
    return False


def update_model_fields_b(model, updates, validator):
    """Version B: Different keys, same fluent pattern."""
    # Same fluent pattern
    model.set_title(updates["title"])\
         .set_content(updates["content"])\
         .set_status("active")\
         .validate()

    if validator.is_valid(model):
        model.save()
        model.notify_observers()
        return True
    return False


def query_database_v1(db, filters, mapper):
    """Version 1: Database query builder pattern."""
    # Method chaining query
    return __extracted_func_287(18, db, mapper)


def query_database_v2(db, filters, mapper):
    """Version 2: Different age threshold, same pattern."""
    # Same query pattern, different threshold
    return __extracted_func_287(21, db, mapper)


def process_stream_v1(stream, parser, handler):
    """Version 1: Stream processing with method calls."""
    # Stream operations
    processed = (stream.filter(lambda x: x.is_valid())
                      .map(parser.parse)
                      .filter(lambda x: x is not None)
                      .take(1000))

    for item in processed:
        handler.process(item)
        handler.update_metrics(item.get_size())
        if handler.should_commit():
            handler.commit()

    return handler.get_statistics()


def process_stream_v2(stream, parser, handler):
    """Version 2: Different take limit, same pattern."""
    # Same stream pattern, different limit
    processed = (stream.filter(lambda x: x.is_valid())
                      .map(parser.parse)
                      .filter(lambda x: x is not None)
                      .take(5000))

    for item in processed:
        handler.process(item)
        handler.update_metrics(item.get_size())
        if handler.should_commit():
            handler.commit()

    return handler.get_statistics()


def build_response_a(data, serializer, cache):
    """Version A: Response builder with multiple method calls."""
    # Building response
    response = serializer.create_response()
    response.set_data(data)
    response.set_status(200)
    response.add_header("Content-Type", "application/json")
    response.add_header("Cache-Control", "max-age=3600")

    # Cache the response
    cache_key = serializer.generate_key(data)
    cache.set(cache_key, response.to_dict())

    return response.build()


def build_response_b(data, serializer, cache):
    """Version B: Different cache duration, same pattern."""
    # Same building pattern
    response = serializer.create_response()
    response.set_data(data)
    response.set_status(200)
    response.add_header("Content-Type", "application/json")
    response.add_header("Cache-Control", "max-age=7200")

    # Cache the response
    cache_key = serializer.generate_key(data)
    cache.set(cache_key, response.to_dict())

    return response.build()


def transform_entity_v1(entity, transformer, validator):
    """Version 1: Entity transformation with validation."""
    # Transform entity
    entity.set_field("processed", True)
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


def transform_entity_v2(entity, transformer, validator):
    """Version 2: Different field name, same pattern."""
    # Same transformation pattern
    entity.set_field("completed", True)
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


def aggregate_results_a(results, aggregator, formatter):
    """Version A: Aggregation with multiple method calls."""
    # Aggregate and format
    total = aggregator.sum([r.get_value() for r in results])
    average = aggregator.average([r.get_value() for r in results])
    maximum = aggregator.max([r.get_value() for r in results])

    formatted = formatter.create_summary()\
                        .add_metric("total", total)\
                        .add_metric("average", average)\
                        .add_metric("maximum", maximum)\
                        .finalize()

    return formatted


def aggregate_results_b(results, aggregator, formatter):
    """Version B: Different metric names, same pattern."""
    # Same aggregation pattern
    total = aggregator.sum([r.get_amount() for r in results])
    average = aggregator.average([r.get_amount() for r in results])
    maximum = aggregator.max([r.get_amount() for r in results])

    formatted = formatter.create_summary()\
                        .add_metric("sum", total)\
                        .add_metric("mean", average)\
                        .add_metric("max", maximum)\
                        .finalize()

    return formatted


def __extracted_func_287(__param_0, db, mapper):
    results = db.table('users').where('age', '>', __param_0).where('status', '=', 'active').order_by('created_at', 'desc').limit(100).get()
    mapped = [mapper.to_dto(row) for row in results]
    validated = [item for item in mapped if item.is_valid()]
    return validated


