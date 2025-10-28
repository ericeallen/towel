"""
Test example: Real-world patterns from common use cases.

Tests patterns commonly found in real applications: API handling,
data validation, ETL pipelines, error handling, and logging.
"""


def handle_api_request_v1(request, auth, rate_limiter, logger):
    """Version 1: Complete API request handling."""
    # Authentication and rate limiting
    if not auth.verify_token(request.headers.get("Authorization")):
        logger.warn("Invalid token")
        return {"error": "Unauthorized"}, 401

    if not rate_limiter.check(request.user_id, limit=100):
        logger.warn(f"Rate limit exceeded for user {request.user_id}")
        return {"error": "Too many requests"}, 429

    # Validate and process
    data = request.json()
    validated = auth.validate_payload(data)

    if not validated:
        logger.error("Invalid payload")
        return {"error": "Bad request"}, 400

    logger.info(f"Processing request for user {request.user_id}")
    return {"status": "success"}, 200


def handle_api_request_v2(request, auth, rate_limiter, logger):
    """Version 2: Different rate limit, same handling pattern."""
    # Same pattern, different limit
    if not auth.verify_token(request.headers.get("Authorization")):
        logger.warn("Invalid token")
        return {"error": "Unauthorized"}, 401

    if not rate_limiter.check(request.user_id, limit=200):
        logger.warn(f"Rate limit exceeded for user {request.user_id}")
        return {"error": "Too many requests"}, 429

    # Validate and process
    data = request.json()
    validated = auth.validate_payload(data)

    if not validated:
        logger.error("Invalid payload")
        return {"error": "Bad request"}, 400

    logger.info(f"Processing request for user {request.user_id}")
    return {"status": "success"}, 200


def etl_pipeline_extract_a(source, config, logger):
    """Version A: ETL extract phase."""
    logger.info("Starting extract phase")

    # Extract with retry logic
    max_retries = 3
    retry_count = 0
    data = None

    while retry_count < max_retries:
        try:
            data = source.fetch(
                endpoint=config["endpoint"],
                params=config["params"],
                timeout=30
            )
            logger.info(f"Extracted {len(data)} records")
            break
        except Exception as e:
            retry_count += 1
            logger.warn(f"Extract failed (attempt {retry_count}): {e}")
            if retry_count >= max_retries:
                logger.error("Extract phase failed")
                raise

    return data


def etl_pipeline_extract_b(source, config, logger):
    """Version B: Different timeout, same extraction pattern."""
    logger.info("Starting extract phase")

    # Same retry logic, different timeout
    max_retries = 3
    retry_count = 0
    data = None

    while retry_count < max_retries:
        try:
            data = source.fetch(
                endpoint=config["endpoint"],
                params=config["params"],
                timeout=60
            )
            logger.info(f"Extracted {len(data)} records")
            break
        except Exception as e:
            retry_count += 1
            logger.warn(f"Extract failed (attempt {retry_count}): {e}")
            if retry_count >= max_retries:
                logger.error("Extract phase failed")
                raise

    return data


def validate_business_rules_v1(data, rules_engine, audit_log):
    """Version 1: Business rule validation."""
    errors = []
    warnings = []

    # Validate required fields
    required = ["customer_id", "amount", "currency"]
    for field in required:
        if field not in data or not data[field]:
            errors.append(f"Missing required field: {field}")
            audit_log.record("validation_error", field=field)

    # Business rules
    if data.get("amount", 0) > 10000:
        if not rules_engine.check_approval(data):
            errors.append("Amount requires approval")
            audit_log.record("approval_required", amount=data["amount"])

    # Warnings
    if data.get("currency") not in ["USD", "EUR", "GBP"]:
        warnings.append(f"Unusual currency: {data.get('currency')}")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def validate_business_rules_v2(data, rules_engine, audit_log):
    """Version 2: Different amount threshold, same validation."""
    errors = []
    warnings = []

    # Validate required fields
    required = ["customer_id", "amount", "currency"]
    for field in required:
        if field not in data or not data[field]:
            errors.append(f"Missing required field: {field}")
            audit_log.record("validation_error", field=field)

    # Business rules (different threshold)
    if data.get("amount", 0) > 5000:
        if not rules_engine.check_approval(data):
            errors.append("Amount requires approval")
            audit_log.record("approval_required", amount=data["amount"])

    # Warnings
    if data.get("currency") not in ["USD", "EUR", "GBP"]:
        warnings.append(f"Unusual currency: {data.get('currency')}")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def process_batch_with_errors_a(items, processor, error_handler, metrics):
    """Version A: Batch processing with error handling."""
    processed = []
    failed = []

    for i, item in enumerate(items):
        try:
            # Process item
            validated = processor.validate(item)
            if not validated:
                failed.append({"index": i, "error": "Validation failed", "item": item})
                metrics.increment("validation_failures")
                continue

            result = processor.transform(item)
            processor.enrich(result)
            processed.append(result)
            metrics.increment("processed")

        except Exception as e:
            error_handler.log(e, context={"index": i, "item": item})
            failed.append({"index": i, "error": str(e), "item": item})
            metrics.increment("processing_errors")

    return {"processed": processed, "failed": failed, "total": len(items)}


def process_batch_with_errors_b(items, processor, error_handler, metrics):
    """Version B: Different metric names, same pattern."""
    processed = []
    failed = []

    for i, item in enumerate(items):
        try:
            # Same pattern
            validated = processor.validate(item)
            if not validated:
                failed.append({"index": i, "error": "Validation failed", "item": item})
                metrics.increment("invalid_items")
                continue

            result = processor.transform(item)
            processor.enrich(result)
            processed.append(result)
            metrics.increment("success_count")

        except Exception as e:
            error_handler.log(e, context={"index": i, "item": item})
            failed.append({"index": i, "error": str(e), "item": item})
            metrics.increment("error_count")

    return {"processed": processed, "failed": failed, "total": len(items)}


def cache_with_fallback_v1(key, cache, database, ttl):
    """Version 1: Cache with database fallback."""
    # Try cache first
    cached = cache.get(key)
    if cached is not None:
        cache.increment_hits()
        return cached

    cache.increment_misses()

    # Fallback to database
    try:
        data = database.query(key)
        if data:
            # Cache for 3600 seconds
            cache.set(key, data, ttl=3600)
            cache.record_fill(key)
            return data
    except Exception as e:
        cache.record_error(e)
        raise

    return None


def cache_with_fallback_v2(key, cache, database, ttl):
    """Version 2: Different TTL, same caching pattern."""
    # Same pattern, different TTL
    cached = cache.get(key)
    if cached is not None:
        cache.increment_hits()
        return cached

    cache.increment_misses()

    # Fallback to database
    try:
        data = database.query(key)
        if data:
            # Cache for 7200 seconds
            cache.set(key, data, ttl=7200)
            cache.record_fill(key)
            return data
    except Exception as e:
        cache.record_error(e)
        raise

    return None


def aggregate_metrics_a(events, time_window, aggregator):
    """Version A: Time-series aggregation."""
    return __extracted_func_287(2, events, time_window)


def aggregate_metrics_b(events, time_window, aggregator):
    """Version B: Different multiplier, same aggregation."""
    return __extracted_func_287(3, events, time_window)


def __extracted_func_287(__param_351, events, time_window):
    buckets = {}
    for event in events:
        timestamp = event['timestamp']
        bucket_key = timestamp // time_window * time_window
        if bucket_key not in buckets:
            buckets[bucket_key] = {'count': 0, 'sum': 0, 'values': []}
        value = event['value'] * __param_351
        buckets[bucket_key]['count'] += 1
        buckets[bucket_key]['sum'] += value
        buckets[bucket_key]['values'].append(value)
    return {k: {'count': v['count'], 'sum': v['sum'], 'avg': v['sum'] / v['count'] if v['count'] > 0 else 0} for k, v in buckets.items()}


