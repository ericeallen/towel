"""
Data processing module.
"""


def calculate_statistics(values):
    """Calculate statistics for a list of values."""
    # Statistics calculation (DUPLICATE across subdirectories!)
    if not values:
        return {"count": 0, "sum": 0, "mean": 0}

    total = sum(values)
    count = len(values)
    mean = total / count

    return {"count": count, "sum": total, "mean": mean}


def process_data(data_list):
    """Process a list of data."""
    numeric_values = [x for x in data_list if isinstance(x, (int, float))]
    stats = calculate_statistics(numeric_values)
    return {"stats": stats, "processed_count": len(data_list)}
