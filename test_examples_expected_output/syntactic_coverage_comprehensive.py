"""
Comprehensive syntactic coverage test.

Tests every Python syntactic construct that can appear in code blocks,
ensuring the refactoring system handles all edge cases correctly.
"""

# =============================================================================
# 1. LITERALS AND BASIC DATA STRUCTURES
# =============================================================================


def __extracted_func_28(__param_0, __param_1):
    result = __param_0 and __param_1 or __param_0
    result = result and __param_0 + __param_1 or __param_0 - __param_1
    return result


def __extracted_func_27(__param_0, __param_1):
    result = len(f'{__param_0 + __param_1}')
    result += len(f'{__param_0 * 2:04d}')
    result += len(f'{__param_0:.2f}')
    return result


def __extracted_func_26(__param_0, __param_1):
    result = __param_0 * 2 if __param_0 > __param_1 else __param_0
    result = result + 10 if result < 100 else result - 10
    return result


def __extracted_func_25(__param_0):
    result = sum((x * 2 for x in __param_0))
    result += sum((x for x in __param_0 if x > 0))
    return result


def __extracted_func_24(__param_0):
    result = sum({i: x * 2 for i, x in enumerate(__param_0)}.values())
    result += sum({i: x for i, x in enumerate(__param_0) if x > 0}.values())
    return result


def __extracted_func_23(__param_0):
    result = sum({x * 2 for x in __param_0})
    result += sum({x for x in __param_0 if x > 0})
    return result


def __extracted_func_22(__param_0, __param_1, __param_2):
    result = (__param_0 + __param_1) * 2 + sum([i * 2 for i in __param_2 if i > __param_0])
    result += len([i for i in __param_2 if __param_0 < i < __param_1])
    result += sum(map(lambda i: i ** 2, filter(lambda i: i > 0, __param_2)))
    return result


def __extracted_func_21(__param_0, __param_1):
    result = 1 if __param_0 is None else 0
    result += 1 if __param_0 is not None else 0
    result += 1 if __param_0 is __param_1 else 0
    return result


def __extracted_func_20(__param_0, __param_1):
    result = 1 if __param_0 in __param_1 else 0
    result += 1 if __param_0 not in __param_1 else 0
    result += 1 if 'key' in {'key': __param_0} else 0
    return result


def __extracted_func_19(__param_0):
    result = __param_0[0][0]
    result += __param_0[1]['key']
    result += __param_0[2][0][1]
    return result


def __extracted_func_18(__param_0, __param_1, __param_2):
    result = 1 if __param_0 < __param_1 < __param_2 else 0
    result += 1 if __param_0 <= __param_1 <= __param_2 else 0
    result += 1 if __param_0 == __param_1 == __param_2 else 0
    return result


def __extracted_func_17(__param_0):
    result = __param_0
    with DummyContext(10) as value:
        result += value
    return result


def __extracted_func_16(__param_0, __param_1, __param_2, __param_3, s1):
    s2 = __param_0 % (__param_1, __param_2)
    s3 = __param_3.format(__param_1, __param_2)
    result = len(s1) + len(s2) + len(s3)
    return result


def __extracted_func_15(__param_0):
    mapper = lambda x: x * 2
    result = sum(map(mapper, __param_0))
    result += sum(map(lambda x: x + 1, __param_0))
    return result


def __extracted_func_14(__param_0):
    result = sum([x * 2 for x in __param_0])
    result += sum([x for x in __param_0 if x > 0])
    result += sum([x + y for x in __param_0 for y in __param_0])
    return result


def __extracted_func_13(__param_0):
    result = __param_0.value
    result += __param_0.data[0]
    result += __param_0.get_value()
    return result


def __extracted_func_12(__param_0):
    x, y = __param_0[:2]
    result = x + y
    a, b, c = __param_0[:3]
    result += a + b + c
    return result


def __extracted_func_11(__param_0, __param_1):
    result = __param_0 and __param_1
    result = result or __param_1
    result = not result
    return result


def __extracted_func_10(__param_0, __param_1):
    result = total = __param_0 + __param_1
    result += total
    a = b = c = __param_0
    result += a + b + c
    return result


def __extracted_func_9(__param_0):
    data = [__param_0, __param_0 + 1, __param_0 + 2]
    data = data + list((__param_0, __param_0 + 1))
    data = data + list({__param_0, __param_0 + 1})
    data = data + list({'a': __param_0, 'b': __param_0 + 1}.values())
    return sum(data)


def __extracted_func_8(__param_0):
    result = 0
    if (n := len(__param_0)) > 5:
        result += n
    if (total := sum(__param_0)) > 10:
        result += total
    return result


def __extracted_func_7(__param_0, __param_1):
    result = helper(__param_0, __param_1)
    result += helper(__param_0, __param_1, 20)
    result += helper(__param_0, __param_1, c=30)
    result += helper(__param_0, __param_1, 40, 50)
    result += helper(__param_0, __param_1, d=60)
    return result


def __extracted_func_6(__param_0):
    result = __param_0[0]
    result += __param_0[-1]
    result += sum(__param_0[1:3])
    result += sum(__param_0[::2])
    result += sum(__param_0[::-1])
    return result


def __extracted_func_5(__param_0, __param_1):
    result = __param_0 & __param_1
    result = result | __param_1
    result = result ^ __param_1
    result = ~result
    result = result << 1
    result = result >> 1
    return result


def __extracted_func_4(__param_0, __param_1):
    result = __param_0 + __param_1
    result = result - __param_1
    result = result * 2
    result = result / 2
    result = result // 2
    result = result % 3
    result = result ** 2
    return result


def __extracted_func_3(__param_0):
    result = __param_0 + 42
    result = result + 3.14
    result = result + 1j
    result = result + len('string')
    result = result + len(b'bytes')
    result = result + (1 if True else 0)
    result = result + (0 if None else 1)
    return result


def __extracted_func_2(__param_0):
    result = __param_0
    result += 10
    result -= 5
    result *= 2
    result /= 2
    result //= 2
    result %= 3
    result **= 2
    return result


def __extracted_func_1(__param_0, __param_1):
    result = 1 if __param_0 == __param_1 else 0
    result += 1 if __param_0 != __param_1 else 0
    result += 1 if __param_0 < __param_1 else 0
    result += 1 if __param_0 > __param_1 else 0
    result += 1 if __param_0 <= __param_1 else 0
    result += 1 if __param_0 >= __param_1 else 0
    result += 1 if __param_0 is __param_1 else 0
    result += 1 if __param_0 is not __param_1 else 0
    return result


def __extracted_func_0(__param_0):
    result = __param_0
    try:
        result = result / __param_0
    except ZeroDivisionError:
        result = 0
    except Exception as e:
        result = -1
    else:
        result += 10
    finally:
        result += 1
    return result


def literals_a(x):
    """Test all literal types."""
    return __extracted_func_3(x)


def literals_b(y):
    """Test all literal types."""
    return __extracted_func_3(y)


# =============================================================================
# 2. COLLECTION LITERALS
# =============================================================================


def collections_a(x):
    """Test collection literal construction."""
    return __extracted_func_9(x)


def collections_b(y):
    """Test collection literal construction."""
    return __extracted_func_9(y)


# =============================================================================
# 3. ARITHMETIC OPERATIONS
# =============================================================================


def arithmetic_a(x, y):
    """Test all arithmetic operators."""
    return __extracted_func_4(x, y)


def arithmetic_b(a, b):
    """Test all arithmetic operators."""
    return __extracted_func_4(a, b)


# =============================================================================
# 4. COMPARISON OPERATIONS
# =============================================================================


def comparisons_a(x, y):
    """Test all comparison operators."""
    return __extracted_func_1(x, y)


def comparisons_b(a, b):
    """Test all comparison operators."""
    return __extracted_func_1(a, b)


# =============================================================================
# 5. LOGICAL OPERATIONS
# =============================================================================


def logical_a(x, y):
    """Test logical operators."""
    return __extracted_func_11(x, y)


def logical_b(a, b):
    """Test logical operators."""
    return __extracted_func_11(a, b)


# =============================================================================
# 6. BITWISE OPERATIONS
# =============================================================================


def bitwise_a(x, y):
    """Test bitwise operators."""
    return __extracted_func_5(x, y)


def bitwise_b(a, b):
    """Test bitwise operators."""
    return __extracted_func_5(a, b)


# =============================================================================
# 7. AUGMENTED ASSIGNMENTS
# =============================================================================


def augmented_a(x):
    """Test augmented assignment operators."""
    return __extracted_func_2(x)


def augmented_b(y):
    """Test augmented assignment operators."""
    return __extracted_func_2(y)


# =============================================================================
# 8. SEQUENCE UNPACKING
# =============================================================================


def unpacking_a(data):
    """Test various unpacking patterns."""
    result = __extracted_func_12(data)
    first, *rest = data
    result += first + sum(rest)
    return result


def unpacking_b(items):
    """Test various unpacking patterns."""
    output = __extracted_func_12(items)
    head, *tail = items
    output += head + sum(tail)
    return output


# =============================================================================
# 9. SUBSCRIPT AND SLICING
# =============================================================================


def subscript_a(data):
    """Test subscript and slice operations."""
    return __extracted_func_6(data)


def subscript_b(items):
    """Test subscript and slice operations."""
    return __extracted_func_6(items)


# =============================================================================
# 10. ATTRIBUTE ACCESS
# =============================================================================


class TestClass:
    def __init__(self, value):
        self.value = value
        self.data = [value]

    def get_value(self):
        return self.value


def attributes_a(obj):
    """Test attribute access."""
    return __extracted_func_13(obj)


def attributes_b(thing):
    """Test attribute access."""
    return __extracted_func_13(thing)


# =============================================================================
# 11. FUNCTION CALLS
# =============================================================================


def helper(a, b, c=10, *args, **kwargs):
    return a + b + c + sum(args) + sum(kwargs.values())


def calls_a(x, y):
    """Test various function call patterns."""
    return __extracted_func_7(x, y)


def calls_b(a, b):
    """Test various function call patterns."""
    return __extracted_func_7(a, b)


# =============================================================================
# 12. LIST COMPREHENSIONS
# =============================================================================


def list_comp_a(data):
    """Test list comprehensions."""
    return __extracted_func_14(data)


def list_comp_b(items):
    """Test list comprehensions."""
    return __extracted_func_14(items)


# =============================================================================
# 13. SET COMPREHENSIONS
# =============================================================================


def set_comp_a(data):
    """Test set comprehensions."""
    return __extracted_func_23(data)


def set_comp_b(items):
    """Test set comprehensions."""
    return __extracted_func_23(items)


# =============================================================================
# 14. DICT COMPREHENSIONS
# =============================================================================


def dict_comp_a(data):
    """Test dict comprehensions."""
    return __extracted_func_24(data)


def dict_comp_b(items):
    """Test dict comprehensions."""
    return __extracted_func_24(items)


# =============================================================================
# 15. GENERATOR EXPRESSIONS
# =============================================================================


def generator_a(data):
    """Test generator expressions."""
    return __extracted_func_25(data)


def generator_b(items):
    """Test generator expressions."""
    return __extracted_func_25(items)


# =============================================================================
# 16. LAMBDA EXPRESSIONS
# =============================================================================


def lambda_a(data):
    """Test lambda expressions."""
    return __extracted_func_15(data)


def lambda_b(items):
    """Test lambda expressions."""
    return __extracted_func_15(items)


# =============================================================================
# 17. CONDITIONAL EXPRESSIONS (TERNARY)
# =============================================================================


def ternary_a(x, threshold):
    """Test conditional expressions."""
    return __extracted_func_26(x, threshold)


def ternary_b(y, limit):
    """Test conditional expressions."""
    return __extracted_func_26(y, limit)


# =============================================================================
# 18. STRING FORMATTING
# =============================================================================


def string_fmt_a(x, y):
    """Test string formatting."""
    s1 = f"x={x}, y={y}"
    return __extracted_func_16('x=%d, y=%d', x, y, 'x={}, y={}', s1)


def string_fmt_b(a, b):
    """Test string formatting."""
    s1 = f"a={a}, b={b}"
    return __extracted_func_16('a=%d, b=%d', a, b, 'a={}, b={}', s1)


# =============================================================================
# 19. F-STRING EXPRESSIONS
# =============================================================================


def fstring_a(x, y):
    """Test f-string with expressions."""
    result = __extracted_func_27(x, y)
    result += len(f"{x=}")
    return result


def fstring_b(a, b):
    """Test f-string with expressions."""
    output = __extracted_func_27(a, b)
    output += len(f"{a=}")
    return output


# =============================================================================
# 20. EXCEPTION HANDLING
# =============================================================================


def exceptions_a(x):
    """Test exception handling."""
    return __extracted_func_0(x)


def exceptions_b(y):
    """Test exception handling."""
    return __extracted_func_0(y)


# =============================================================================
# 21. WITH STATEMENTS (CONTEXT MANAGERS)
# =============================================================================


class DummyContext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        pass


def context_mgr_a(x):
    """Test context managers."""
    return __extracted_func_17(x)


def context_mgr_b(y):
    """Test context managers."""
    return __extracted_func_17(y)


# =============================================================================
# 22. WALRUS OPERATOR (NAMED EXPRESSIONS)
# =============================================================================


def walrus_a(data):
    """Test walrus operator."""
    return __extracted_func_8(data)


def walrus_b(items):
    """Test walrus operator."""
    return __extracted_func_8(items)


# =============================================================================
# 23. CHAINED COMPARISONS
# =============================================================================


def chained_comp_a(x, y, z):
    """Test chained comparisons."""
    return __extracted_func_18(x, y, z)


def chained_comp_b(a, b, c):
    """Test chained comparisons."""
    return __extracted_func_18(a, b, c)


# =============================================================================
# 24. BOOLEAN SHORT-CIRCUIT
# =============================================================================


def short_circuit_a(x, y):
    """Test boolean short-circuit evaluation."""
    return __extracted_func_28(x, y)


def short_circuit_b(a, b):
    """Test boolean short-circuit evaluation."""
    return __extracted_func_28(a, b)


# =============================================================================
# 25. NESTED DATA STRUCTURES
# =============================================================================


def nested_a(data):
    """Test nested data structure access."""
    return __extracted_func_19(data)


def nested_b(items):
    """Test nested data structure access."""
    return __extracted_func_19(items)


# =============================================================================
# 26. MULTI-TARGET ASSIGNMENT
# =============================================================================


def multi_assign_a(x, y):
    """Test multi-target assignment."""
    return __extracted_func_10(x, y)


def multi_assign_b(m, n):
    """Test multi-target assignment."""
    return __extracted_func_10(m, n)


# =============================================================================
# 27. STARRED EXPRESSIONS
# =============================================================================


def starred_a(data):
    """Test starred expressions."""
    first, *middle, last = data
    result = first + last + sum(middle)
    items = [*data, 99]
    result += sum(items)
    return result


def starred_b(items):
    """Test starred expressions."""
    head, *center, tail = items
    output = head + tail + sum(center)
    vals = [*items, 99]
    output += sum(vals)
    return output


# =============================================================================
# 28. MEMBERSHIP TESTS
# =============================================================================


def membership_a(x, data):
    """Test membership operators."""
    return __extracted_func_20(x, data)


def membership_b(y, items):
    """Test membership operators."""
    return __extracted_func_20(y, items)


# =============================================================================
# 29. IDENTITY TESTS
# =============================================================================


def identity_a(x, y):
    """Test identity operators."""
    return __extracted_func_21(x, y)


def identity_b(a, b):
    """Test identity operators."""
    return __extracted_func_21(a, b)


# =============================================================================
# 30. COMPLEX EXPRESSIONS
# =============================================================================


def complex_expr_a(x, y, data):
    """Test complex nested expressions."""
    return __extracted_func_22(x, y, data)


def complex_expr_b(a, b, items):
    """Test complex nested expressions."""
    return __extracted_func_22(a, b, items)
