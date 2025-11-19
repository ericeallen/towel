"""
Test example: Real-world patterns from common use cases.

Tests patterns commonly found in real applications: API handling,
data validation, ETL pipelines, error handling, and logging.
"""


def handle_api_request_v1(request, auth, rate_limiter, logger):
    """Version 1: Complete API request handling."""
    # Authentication and rate limiting
    return extracted_func(100, auth, extracted_func, logger, rate_limiter, request)


def handle_api_request_v2(request, auth, rate_limiter, logger):
    """Version 2: Different rate limit, same handling pattern."""
    # Same pattern, different limit
    return extracted_func(200, auth, extracted_func, logger, rate_limiter, request)


def etl_pipeline_extract_a(source, config, logger):
    """Version A: ETL extract phase."""
    return extracted_func(30, config, logger, source)


def etl_pipeline_extract_b(source, config, logger):
    """Version B: Different timeout, same extraction pattern."""
    return extracted_func(60, config, logger, source)


def validate_business_rules_v1(data, rules_engine, audit_log):
    """Version 1: Business rule validation."""
    return extracted_func(10000, audit_log, data, rules_engine)


def validate_business_rules_v2(data, rules_engine, audit_log):
    """Version 2: Different amount threshold, same validation."""
    return extracted_func(5000, audit_log, data, rules_engine)


def process_batch_with_errors_a(items, processor, error_handler, metrics):
    """Version A: Batch processing with error handling."""
    processed = []
    failed = []

    extracted_func('validation_failures', 'processed', 'processing_errors', error_handler, failed, items, metrics, processed, processor)

    return {"processed": processed, "failed": failed, "total": len(items)}


def process_batch_with_errors_b(items, processor, error_handler, metrics):
    """Version B: Different metric names, same pattern."""
    processed = []
    failed = []

    extracted_func('invalid_items', 'success_count', 'error_count', error_handler, failed, items, metrics, processed, processor)

    return {"processed": processed, "failed": failed, "total": len(items)}


def cache_with_fallback_v1(key, cache, database, ttl):
    """Version 1: Cache with database fallback."""
    # Try cache first
    return extracted_func(3600, cache, database, key)


def cache_with_fallback_v2(key, cache, database, ttl):
    """Version 2: Different TTL, same caching pattern."""
    # Same pattern, different TTL
    return extracted_func(7200, cache, database, key)


def aggregate_metrics_a(events, time_window, aggregator):
    """Version A: Time-series aggregation."""
    return extracted_func(2, events, time_window)


def aggregate_metrics_b(events, time_window, aggregator):
    """Version B: Different multiplier, same aggregation."""
    return extracted_func(3, events, time_window)


def extracted_func(__param_0, events, time_window):
    buckets = {}
    for event in events:
        timestamp = event['timestamp']
        bucket_key = timestamp // time_window * time_window
        if bucket_key not in buckets:
            buckets[bucket_key] = {'count': 0, 'sum': 0, 'values': []}
        value = event['value'] * __param_0
        buckets[bucket_key]['count'] += 1
        buckets[bucket_key]['sum'] += value
        buckets[bucket_key]['values'].append(value)
    return {k: {'count': v['count'], 'sum': v['sum'], 'avg': v['sum'] / v['count'] if v['count'] > 0 else 0} for k, v in buckets.items()}


def extracted_func(__param_0, audit_log, data, rules_engine):
    errors = []
    warnings = []
    required = ['customer_id', 'amount', 'currency']
    for field in required:
        if field not in data or not data[field]:
            errors.append(f'Missing required field: {field}')
            audit_log.record('validation_error', field=field)
    if data.get('amount', 0) > __param_0:
        if not rules_engine.check_approval(data):
            errors.append('Amount requires approval')
            audit_log.record('approval_required', amount=data['amount'])
    if data.get('currency') not in ['USD', 'EUR', 'GBP']:
        warnings.append(f"Unusual currency: {data.get('currency')}")
    return {'valid': len(errors) == 0, 'errors': errors, 'warnings': warnings}


def extracted_func(__param_0, config, logger, source):
    logger.info('Starting extract phase')
    max_retries = 3
    retry_count = 0
    data = None
    while retry_count < max_retries:
        try:
            data = source.fetch(endpoint=config['endpoint'], params=config['params'], timeout=__param_0)
            logger.info(f'Extracted {len(data)} records')
            break
        except Exception as e:
            retry_count += 1
            logger.warn(f'Extract failed (attempt {retry_count}): {e}')
            if retry_count >= max_retries:
                logger.error('Extract phase failed')
                raise
    return data


def extracted_func(__param_0, cache, database, key):
    cached = cache.get(key)
    if cached is not None:
        cache.increment_hits()
        return cached
    cache.increment_misses()
    try:
        data = database.query(key)
        if data:
            cache.set(key, data, ttl=__param_0)
            cache.record_fill(key)
            return data
    except Exception as e:
        cache.record_error(e)
        raise
    return None


def extracted_func(__param_0, __param_1, __param_2, error_handler, failed, items, metrics, processed, processor):
    for i, item in enumerate(items):
        try:
            validated = processor.validate(item)
            if not validated:
                failed.append({'index': i, 'error': 'Validation failed', 'item': item})
                metrics.increment(__param_0)
                continue
            result = processor.transform(item)
            processor.enrich(result)
            processed.append(result)
            metrics.increment(__param_1)
        except Exception as e:
            error_handler.log(e, context={'index': i, 'item': item})
            failed.append({'index': i, 'error': str(e), 'item': item})
            metrics.increment(__param_2)


def extracted_func(auth, logger, request):
    data = request.json()
    validated = auth.validate_payload(data)
    if not validated:
        logger.error('Invalid payload')
        return ({'error': 'Bad request'}, 400)
    logger.info(f'Processing request for user {request.user_id}')
    return ({'status': 'success'}, 200)


def extracted_func(__param_0, auth, extracted_func, logger, rate_limiter, request):
    if not auth.verify_token(request.headers.get('Authorization')):
        logger.warn('Invalid token')
        return ({'error': 'Unauthorized'}, 401)
    if not rate_limiter.check(request.user_id, limit=__param_0):
        logger.warn(f'Rate limit exceeded for user {request.user_id}')
        return ({'error': 'Too many requests'}, 429)
    return extracted_func(auth, logger, request)














