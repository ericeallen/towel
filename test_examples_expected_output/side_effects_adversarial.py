"""
Adversarial test cases: Side effects and global state mutations.

These examples are designed to break observational equivalence if Towel
doesn't correctly handle side effects during extraction.
"""

# Global state for testing
counter = 0
log = []


def append_and_sum_a(items):
    """Modifies global list while computing sum."""
    global log
    total = 0
    for item in items:
        log.append(item)
        total += item
    return total


def append_and_sum_b(values):
    """Similar to append_and_sum_a - should extract common pattern."""
    global log
    result = 0
    for value in values:
        log.append(value)
        result += value
    return result


# Mutable default argument - classic Python gotcha
def process_with_cache_a(item, cache=[]):
    """Uses mutable default argument."""
    if item in cache:
        return True
    cache.append(item)
    return False


def process_with_cache_b(value, cache=[]):
    """Similar pattern with mutable default."""
    if value in cache:
        return True
    cache.append(value)
    return False


# Side effects in comprehensions
def filter_and_log_a(items):
    """Side effect in list comprehension via global."""
    global counter
    result = []
    for x in items:
        counter += 1
        result.append(x * 2)
    return result


def filter_and_log_b(values):
    """Similar side effect pattern."""
    global counter
    output = []
    for y in values:
        counter += 1
        output.append(y * 2)
    return output


# Mutable object modification
def modify_dict_a(data, key, value):
    """Modifies dict in place and returns it."""
    data[key] = value
    data['modified'] = True
    return data


def modify_dict_b(config, k, v):
    """Similar dict modification pattern."""
    config[k] = v
    config['modified'] = True
    return config


# List mutation
def extend_and_return_a(lst, items):
    """Extends list in place."""
    for item in items:
        lst.append(item)
    return len(lst)


def extend_and_return_b(target, values):
    """Similar list extension pattern."""
    for value in values:
        target.append(value)
    return len(target)


# Multiple mutable arguments
def swap_contents_a(list1, list2):
    """Swaps contents of two lists."""
    temp = list1[:]
    return __extracted_func_210(list1, list2, temp)


def swap_contents_b(a, b):
    """Similar swap pattern."""
    backup = a[:]
    return __extracted_func_210(a, b, backup)


def __extracted_func_210(__param_209, __param_210, __param_211):
    __param_209.clear()
    __param_209.extend(__param_210)
    __param_210.clear()
    __param_210.extend(__param_211)
    return len(__param_209) + len(__param_210)


