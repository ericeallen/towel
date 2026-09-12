"""
Adversarial test cases: Side effects and global state mutations.

These examples are designed to break observational equivalence if Towel
doesn't correctly handle side effects during extraction.
"""

# Global state for testing
counter = 0
log = []


def __extracted_func_4(__param_0, __param_1):
    for item in __param_0:
        __param_1.append(item)
    return len(__param_1)


def __extracted_func_3(__param_0, __param_1, __param_2):
    __param_0[__param_1] = __param_2
    __param_0['modified'] = True
    return __param_0


def __extracted_func_2(__param_0, cache):
    if __param_0 in cache:
        return True
    cache.append(__param_0)
    return False


def __extracted_func_1(__param_0, __param_1):
    temp = __param_0[:]
    __param_0.clear()
    __param_0.extend(__param_1)
    __param_1.clear()
    __param_1.extend(temp)
    return len(__param_0) + len(__param_1)


def __extracted_func_0(__param_0):
    global counter
    result = []
    for x in __param_0:
        counter += 1
        result.append(x * 2)
    return result


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
    return __extracted_func_2(item, cache)


def process_with_cache_b(value, cache=[]):
    """Similar pattern with mutable default."""
    return __extracted_func_2(value, cache)


# Side effects in comprehensions
def filter_and_log_a(items):
    """Side effect in list comprehension via global."""
    return __extracted_func_0(items)


def filter_and_log_b(values):
    """Similar side effect pattern."""
    return __extracted_func_0(values)


# Mutable object modification
def modify_dict_a(data, key, value):
    """Modifies dict in place and returns it."""
    return __extracted_func_3(data, key, value)


def modify_dict_b(config, k, v):
    """Similar dict modification pattern."""
    return __extracted_func_3(config, k, v)


# List mutation
def extend_and_return_a(lst, items):
    """Extends list in place."""
    return __extracted_func_4(items, lst)


def extend_and_return_b(target, values):
    """Similar list extension pattern."""
    return __extracted_func_4(values, target)


# Multiple mutable arguments
def swap_contents_a(list1, list2):
    """Swaps contents of two lists."""
    return __extracted_func_1(list1, list2)


def swap_contents_b(a, b):
    """Similar swap pattern."""
    return __extracted_func_1(a, b)
