"""
Report generation module.
"""


def __extracted_func_0(values):
    if not values:
        return {'count': 0, 'sum': 0, 'mean': 0}
    total = sum(values)
    count = len(values)
    mean = total / count
    return {'count': count, 'sum': total, 'mean': mean}


def calculate_report_stats(values):
    """Calculate statistics for report."""
    # Statistics calculation (DUPLICATE across subdirectories!)
    return __extracted_func_0(values)


def generate_report(measurements):
    """Generate a report from measurements."""
    stats = calculate_report_stats(measurements)
    return {
        'title': 'Measurement Report',
        'statistics': stats,
        'total_measurements': len(measurements)
    }
