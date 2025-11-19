"""
Test cases for global and nonlocal statement handling.

Tests that Towel correctly handles scope modifiers:
- Global statements
- Nonlocal statements
- Proper free variable analysis with these constructs
"""

# Global variable for testing
counter = 0


def test_global_modify_a():
    """Function that modifies global variable."""
    return extracted_func()


def test_global_modify_b():
    """Duplicate with global modification."""
    return extracted_func()


def test_nonlocal_a():
    """Function using nonlocal."""
    return extracted_func()


def test_nonlocal_b():
    """Duplicate with nonlocal."""
    return extracted_func()


def test_nested_nonlocal_a():
    """Nested function with nonlocal."""

    return extracted_func()


def test_nested_nonlocal_b():
    """Duplicate nested nonlocal."""

    return extracted_func()


def test_global_and_local_a(items):
    """Mix of global and local variables."""
    return extracted_func(items)


def test_global_and_local_b(items):
    """Duplicate mix of global and local."""
    return extracted_func(items)


def extracted_func():

    def outer():
        count = 0

        def middle():
            nonlocal count
            count += 1

            def inner():
                nonlocal count
                count += 10
                return count
            return inner
        return middle
    f = outer()
    g = f()
    return g()


def extracted_func():
    total = 0

    def increment(value):
        nonlocal total
        total += value
        return total
    results = [increment(i) for i in range(5)]
    return results


def extracted_func(items):
    global counter
    local_sum = 0
    for item in items:
        counter += 1
        local_sum += item
    return (counter, local_sum)


def extracted_func():
    global counter
    result = []
    for i in range(5):
        counter += 1
        result.append(counter)
    return result








