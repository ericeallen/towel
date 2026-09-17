"""
Data processing module.
"""
from lib.report_generator import calculate_report_stats


def calculate_statistics(values):
    """Calculate statistics for a list of values."""
    # Statistics calculation (DUPLICATE across subdirectories!)
    return calculate_report_stats(values)


def process_data(data_list):
    """Process a list of data."""
    numeric_values = [x for x in data_list if isinstance(x, (int, float))]
    stats = calculate_statistics(numeric_values)
    return {"stats": stats, "processed_count": len(data_list)}
