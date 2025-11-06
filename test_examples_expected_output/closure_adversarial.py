"""
Adversarial test cases: Closures and variable capture.

These examples test whether Towel correctly handles function extraction
when variables are captured from enclosing scopes.
"""


def make_adder_a(x):
    """Returns a closure that adds x."""
    total = 0

    def add(y):
        nonlocal total
        total += y
        return x + total

    return add


def make_adder_b(n):
    """Similar closure pattern."""
    sum_val = 0

    def add_func(m):
        nonlocal sum_val
        sum_val += m
        return n + sum_val

    return add_func


def counter_factory_a(start):
    """Factory that returns counter functions."""
    count = start

    def increment():
        nonlocal count
        count += 1
        return count

    return increment


def counter_factory_b(initial):
    """Similar counter factory."""
    value = initial

    def inc():
        nonlocal value
        value += 1
        return value

    return inc


def accumulator_a(items):
    """Returns accumulator function with closure over items."""
    index = 0
    total = 0

    def next_sum():
        nonlocal index, total
        if index < len(items):
            total += items[index]
            index += 1
        return total

    return next_sum


def accumulator_b(values):
    """Similar accumulator pattern."""
    idx = 0
    sum_val = 0

    def next_total():
        nonlocal idx, sum_val
        if idx < len(values):
            sum_val += values[idx]
            idx += 1
        return sum_val

    return next_total


# These should NOT be extractable because they capture different variables
def multiplier_a(x, factor):
    """Multiplier with captured factor."""
    return extracted_func(x, factor)


def multiplier_b(y, multiplier):
    """Similar pattern but captures different variable names."""
    return extracted_func(y, multiplier)


# Variable shadowing edge case
def shadow_test_a(x):
    """Tests variable shadowing."""
    result = x
    x = x + 1
    result = result + x
    return result


def shadow_test_b(y):
    """Similar shadowing pattern."""
    output = y
    y = y + 1
    output = output + y
    return output


def extracted_func(__param_0, __param_1):
    result = 0
    for i in range(__param_0):
        result += __param_1
    return result


