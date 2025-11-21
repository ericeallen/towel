"""
Example 4: More complex repeated code with data processing.

Tests: Same-file duplicate detection with loops and complex logic.
"""


def process_json_data(data):
    """Process JSON data."""
    # Data processing logic
    return __extracted_func_0(data)


def process_xml_data(data):
    """Process XML data."""
    # Data processing logic (DUPLICATE!)
    return __extracted_func_0(data)


def process_csv_data(data):
    """Process CSV data."""
    # Data processing logic (DUPLICATE!)
    return __extracted_func_0(data)


def __extracted_func_0(data):
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = value.strip().lower()
        elif isinstance(value, (int, float)):
            result[key] = value * 2
        elif isinstance(value, list):
            result[key] = [str(item) for item in value]
        else:
            result[key] = str(value)
    return result


