"""
Comprehensive syntactic coverage test.

Tests every Python syntactic construct that can appear in code blocks,
ensuring the refactoring system handles all edge cases correctly.
"""

# =============================================================================
# 1. LITERALS AND BASIC DATA STRUCTURES
# =============================================================================


def __extracted_func_23(__param_0, __param_1, _towel_owner):
    result = __param_0 and __param_1 or __param_0
    result = result and __param_0 + __param_1 or __param_0 - __param_1
    return result


def __extracted_func_22(__param_0, __param_1, _towel_owner):
    result = __param_0 * 2 if __param_0 > __param_1 else __param_0
    result = result + 10 if result < 100 else result - 10
    return result


def __extracted_func_21(__param_0, _towel_owner):
    result = sum((x * 2 for x in __param_0))
    result += sum((x for x in __param_0 if x > 0))
    return result


def __extracted_func_20(__param_0, _towel_owner):
    result = sum({i: x * 2 for i, x in enumerate(__param_0)}.values())
    result += sum({i: x for i, x in enumerate(__param_0) if x > 0}.values())
    return result


def __extracted_func_19(__param_0, _towel_owner):
    result = sum({x * 2 for x in __param_0})
    result += sum({x for x in __param_0 if x > 0})
    return result


def __extracted_func_18(__param_0, __param_1, _towel_owner):
    result = 1 if __param_0 is None else 0
    result += 1 if __param_0 is not None else 0
    result += 1 if __param_0 is __param_1 else 0
    return result


def __extracted_func_17(__param_0, __param_1, _towel_owner):
    result = 1 if __param_0 in __param_1 else 0
    result += 1 if __param_0 not in __param_1 else 0
    result += 1 if 'key' in {'key': __param_0} else 0
    return result


def __extracted_func_16(__param_0, _towel_owner):
    result = __param_0[0][0]
    result += __param_0[1]['key']
    result += __param_0[2][0][1]
    return result


def __extracted_func_15(__param_0, __param_1, __param_2, _towel_owner):
    result = 1 if __param_0 < __param_1 < __param_2 else 0
    result += 1 if __param_0 <= __param_1 <= __param_2 else 0
    result += 1 if __param_0 == __param_1 == __param_2 else 0
    return result


def __extracted_func_14(__param_0, _towel_owner):
    result = __param_0
    with DummyContext(10) as value:
        result += value
    return result


def __extracted_func_13(__param_0, _towel_owner):
    mapper = lambda x: x * 2
    result = sum(map(mapper, __param_0))
    result += sum(map(lambda x: x + 1, __param_0))
    return result


def __extracted_func_12(__param_0, _towel_owner):
    result = __param_0.value
    result += __param_0.data[0]
    result += __param_0.get_value()
    return result


def __extracted_func_11(__param_0, __param_1, _towel_owner):
    result = __param_0 and __param_1
    result = result or __param_1
    result = not result
    return result


def __extracted_func_10(__param_0, __param_1, _towel_owner):
    result = total = __param_0 + __param_1
    result += total
    a = b = c = __param_0
    result += a + b + c
    return result


def __extracted_func_9(__param_0, _towel_owner):
    data = [__param_0, __param_0 + 1, __param_0 + 2]  # list
    data = data + list((__param_0, __param_0 + 1))  # tuple
    data = data + list({__param_0, __param_0 + 1})  # set
    data = data + list({'a': __param_0, 'b': __param_0 + 1}.values())  # dict
    return sum(data)


def __extracted_func_8(__param_0, _towel_owner):
    result = 0
    if (n := len(__param_0)) > 5:
        result += n
    if (total := sum(__param_0)) > 10:
        result += total
    return result


def __extracted_func_7(__param_0, __param_1, _towel_owner):
    result = helper(__param_0, __param_1)  # positional
    result += helper(__param_0, __param_1, 20)  # with optional
    result += helper(__param_0, __param_1, c=30)  # keyword
    result += helper(__param_0, __param_1, 40, 50)  # *args
    result += helper(__param_0, __param_1, d=60)  # **kwargs
    return result


def __extracted_func_6(__param_0, _towel_owner):
    result = __param_0[0]  # simple subscript
    result += __param_0[-1]  # negative index
    result += sum(__param_0[1:3])  # slice
    result += sum(__param_0[::2])  # step slice
    result += sum(__param_0[::-1])  # reverse
    return result


def __extracted_func_5(__param_0, __param_1, _towel_owner):
    result = __param_0 & __param_1  # and
    result = result | __param_1  # or
    result = result ^ __param_1  # xor
    result = ~result  # not
    result = result << 1  # left shift
    result = result >> 1  # right shift
    return result


def __extracted_func_4(__param_0, __param_1, _towel_owner):
    result = __param_0 + __param_1  # addition
    result = result - __param_1  # subtraction
    result = result * 2  # multiplication
    result = result / 2  # division
    result = result // 2  # floor division
    result = result % 3  # modulo
    result = result ** 2  # exponentiation
    return result


def __extracted_func_3(__param_0, _towel_owner):
    result = __param_0 + 42  # int
    result = result + 3.14  # float
    result = result + 1j  # complex
    result = result + len('string')  # string
    result = result + len(b'bytes')  # bytes
    result = result + (1 if True else 0)  # boolean
    result = result + (0 if None else 1)  # None
    return result


def __extracted_func_2(__param_0, _towel_owner):
    result = __param_0
    result += 10
    result -= 5
    result *= 2
    result /= 2
    result //= 2
    result %= 3
    result **= 2
    return result


def __extracted_func_1(__param_0, __param_1, _towel_owner):
    result = 1 if __param_0 == __param_1 else 0
    result += 1 if __param_0 != __param_1 else 0
    result += 1 if __param_0 < __param_1 else 0
    result += 1 if __param_0 > __param_1 else 0
    result += 1 if __param_0 <= __param_1 else 0
    result += 1 if __param_0 >= __param_1 else 0
    result += 1 if __param_0 is __param_1 else 0
    result += 1 if __param_0 is not __param_1 else 0
    return result


def __extracted_func_0(__param_0, _towel_owner):
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
    _towel_arguments_3 = [(x,)]
    del x
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


def literals_b(y):
    """Test all literal types."""
    _towel_arguments_3 = [(y,)]
    del y
    return __extracted_func_3(_towel_arguments_3[0][0], _towel_arguments_3.pop())


# =============================================================================
# 2. COLLECTION LITERALS
# =============================================================================


def collections_a(x):
    """Test collection literal construction."""
    _towel_arguments_9 = [(x,)]
    del x
    return __extracted_func_9(_towel_arguments_9[0][0], _towel_arguments_9.pop())


def collections_b(y):
    """Test collection literal construction."""
    _towel_arguments_9 = [(y,)]
    del y
    return __extracted_func_9(_towel_arguments_9[0][0], _towel_arguments_9.pop())


# =============================================================================
# 3. ARITHMETIC OPERATIONS
# =============================================================================


def arithmetic_a(x, y):
    """Test all arithmetic operators."""
    _towel_arguments_4 = [(y, x)]
    del x
    del y
    return __extracted_func_4(_towel_arguments_4[0][1], _towel_arguments_4[0][0], _towel_arguments_4.pop())


def arithmetic_b(a, b):
    """Test all arithmetic operators."""
    _towel_arguments_4 = [(b, a)]
    del a
    del b
    return __extracted_func_4(_towel_arguments_4[0][1], _towel_arguments_4[0][0], _towel_arguments_4.pop())


# =============================================================================
# 4. COMPARISON OPERATIONS
# =============================================================================


def comparisons_a(x, y):
    """Test all comparison operators."""
    _towel_arguments_1 = [(y, x)]
    del x
    del y
    return __extracted_func_1(_towel_arguments_1[0][1], _towel_arguments_1[0][0], _towel_arguments_1.pop())


def comparisons_b(a, b):
    """Test all comparison operators."""
    _towel_arguments_1 = [(b, a)]
    del a
    del b
    return __extracted_func_1(_towel_arguments_1[0][1], _towel_arguments_1[0][0], _towel_arguments_1.pop())


# =============================================================================
# 5. LOGICAL OPERATIONS
# =============================================================================


def logical_a(x, y):
    """Test logical operators."""
    _towel_arguments_11 = [(y, x)]
    del x
    del y
    return __extracted_func_11(_towel_arguments_11[0][1], _towel_arguments_11[0][0], _towel_arguments_11.pop())


def logical_b(a, b):
    """Test logical operators."""
    _towel_arguments_11 = [(b, a)]
    del a
    del b
    return __extracted_func_11(_towel_arguments_11[0][1], _towel_arguments_11[0][0], _towel_arguments_11.pop())


# =============================================================================
# 6. BITWISE OPERATIONS
# =============================================================================


def bitwise_a(x, y):
    """Test bitwise operators."""
    _towel_arguments_5 = [(y, x)]
    del x
    del y
    return __extracted_func_5(_towel_arguments_5[0][1], _towel_arguments_5[0][0], _towel_arguments_5.pop())


def bitwise_b(a, b):
    """Test bitwise operators."""
    _towel_arguments_5 = [(b, a)]
    del a
    del b
    return __extracted_func_5(_towel_arguments_5[0][1], _towel_arguments_5[0][0], _towel_arguments_5.pop())


# =============================================================================
# 7. AUGMENTED ASSIGNMENTS
# =============================================================================


def augmented_a(x):
    """Test augmented assignment operators."""
    _towel_arguments_2 = [(x,)]
    del x
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


def augmented_b(y):
    """Test augmented assignment operators."""
    _towel_arguments_2 = [(y,)]
    del y
    return __extracted_func_2(_towel_arguments_2[0][0], _towel_arguments_2.pop())


# =============================================================================
# 8. SEQUENCE UNPACKING
# =============================================================================


def unpacking_a(data):
    """Test various unpacking patterns."""
    x, y = data[:2]
    result = x + y
    a, b, c = data[:3]
    result += a + b + c
    first, *rest = data
    result += first + sum(rest)
    return result


def unpacking_b(items):
    """Test various unpacking patterns."""
    p, q = items[:2]
    output = p + q
    m, n, o = items[:3]
    output += m + n + o
    head, *tail = items
    output += head + sum(tail)
    return output


# =============================================================================
# 9. SUBSCRIPT AND SLICING
# =============================================================================


def subscript_a(data):
    """Test subscript and slice operations."""
    _towel_arguments_6 = [(data,)]
    del data
    return __extracted_func_6(_towel_arguments_6[0][0], _towel_arguments_6.pop())


def subscript_b(items):
    """Test subscript and slice operations."""
    _towel_arguments_6 = [(items,)]
    del items
    return __extracted_func_6(_towel_arguments_6[0][0], _towel_arguments_6.pop())


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
    _towel_arguments_12 = [(obj,)]
    del obj
    return __extracted_func_12(_towel_arguments_12[0][0], _towel_arguments_12.pop())


def attributes_b(thing):
    """Test attribute access."""
    _towel_arguments_12 = [(thing,)]
    del thing
    return __extracted_func_12(_towel_arguments_12[0][0], _towel_arguments_12.pop())


# =============================================================================
# 11. FUNCTION CALLS
# =============================================================================


def helper(a, b, c=10, *args, **kwargs):
    return a + b + c + sum(args) + sum(kwargs.values())


def calls_a(x, y):
    """Test various function call patterns."""
    _towel_arguments_7 = [(y, x)]
    del x
    del y
    return __extracted_func_7(_towel_arguments_7[0][1], _towel_arguments_7[0][0], _towel_arguments_7.pop())


def calls_b(a, b):
    """Test various function call patterns."""
    _towel_arguments_7 = [(b, a)]
    del a
    del b
    return __extracted_func_7(_towel_arguments_7[0][1], _towel_arguments_7[0][0], _towel_arguments_7.pop())


# =============================================================================
# 12. LIST COMPREHENSIONS
# =============================================================================


def list_comp_a(data):
    """Test list comprehensions."""
    result = sum([x * 2 for x in data])
    result += sum([x for x in data if x > 0])
    result += sum([x + y for x in data for y in data])
    return result


def list_comp_b(items):
    """Test list comprehensions."""
    output = sum([x * 2 for x in items])
    output += sum([x for x in items if x > 0])
    output += sum([x + y for x in items for y in items])
    return output


# =============================================================================
# 13. SET COMPREHENSIONS
# =============================================================================


def set_comp_a(data):
    """Test set comprehensions."""
    _towel_arguments_19 = [(data,)]
    del data
    return __extracted_func_19(_towel_arguments_19[0][0], _towel_arguments_19.pop())


def set_comp_b(items):
    """Test set comprehensions."""
    _towel_arguments_19 = [(items,)]
    del items
    return __extracted_func_19(_towel_arguments_19[0][0], _towel_arguments_19.pop())


# =============================================================================
# 14. DICT COMPREHENSIONS
# =============================================================================


def dict_comp_a(data):
    """Test dict comprehensions."""
    _towel_arguments_20 = [(data,)]
    del data
    return __extracted_func_20(_towel_arguments_20[0][0], _towel_arguments_20.pop())


def dict_comp_b(items):
    """Test dict comprehensions."""
    _towel_arguments_20 = [(items,)]
    del items
    return __extracted_func_20(_towel_arguments_20[0][0], _towel_arguments_20.pop())


# =============================================================================
# 15. GENERATOR EXPRESSIONS
# =============================================================================


def generator_a(data):
    """Test generator expressions."""
    _towel_arguments_21 = [(data,)]
    del data
    return __extracted_func_21(_towel_arguments_21[0][0], _towel_arguments_21.pop())


def generator_b(items):
    """Test generator expressions."""
    _towel_arguments_21 = [(items,)]
    del items
    return __extracted_func_21(_towel_arguments_21[0][0], _towel_arguments_21.pop())


# =============================================================================
# 16. LAMBDA EXPRESSIONS
# =============================================================================


def lambda_a(data):
    """Test lambda expressions."""
    _towel_arguments_13 = [(data,)]
    del data
    return __extracted_func_13(_towel_arguments_13[0][0], _towel_arguments_13.pop())


def lambda_b(items):
    """Test lambda expressions."""
    _towel_arguments_13 = [(items,)]
    del items
    return __extracted_func_13(_towel_arguments_13[0][0], _towel_arguments_13.pop())


# =============================================================================
# 17. CONDITIONAL EXPRESSIONS (TERNARY)
# =============================================================================


def ternary_a(x, threshold):
    """Test conditional expressions."""
    _towel_arguments_22 = [(threshold, x)]
    del x
    del threshold
    return __extracted_func_22(_towel_arguments_22[0][1], _towel_arguments_22[0][0], _towel_arguments_22.pop())


def ternary_b(y, limit):
    """Test conditional expressions."""
    _towel_arguments_22 = [(limit, y)]
    del y
    del limit
    return __extracted_func_22(_towel_arguments_22[0][1], _towel_arguments_22[0][0], _towel_arguments_22.pop())


# =============================================================================
# 18. STRING FORMATTING
# =============================================================================


def string_fmt_a(x, y):
    """Test string formatting."""
    s1 = f"x={x}, y={y}"
    s2 = "x=%d, y=%d" % (x, y)
    s3 = "x={}, y={}".format(x, y)
    result = len(s1) + len(s2) + len(s3)
    return result


def string_fmt_b(a, b):
    """Test string formatting."""
    s1 = f"a={a}, b={b}"
    s2 = "a=%d, b=%d" % (a, b)
    s3 = "a={}, b={}".format(a, b)
    output = len(s1) + len(s2) + len(s3)
    return output


# =============================================================================
# 19. F-STRING EXPRESSIONS
# =============================================================================


def fstring_a(x, y):
    """Test f-string with expressions."""
    result = len(f"{x + y}")
    result += len(f"{x * 2:04d}")
    result += len(f"{x:.2f}")
    result += len(f"{x=}")
    return result


def fstring_b(a, b):
    """Test f-string with expressions."""
    output = len(f"{a + b}")
    output += len(f"{a * 2:04d}")
    output += len(f"{a:.2f}")
    output += len(f"{a=}")
    return output


# =============================================================================
# 20. EXCEPTION HANDLING
# =============================================================================


def exceptions_a(x):
    """Test exception handling."""
    _towel_arguments = [(x,)]
    del x
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


def exceptions_b(y):
    """Test exception handling."""
    _towel_arguments = [(y,)]
    del y
    return __extracted_func_0(_towel_arguments[0][0], _towel_arguments.pop())


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
    _towel_arguments_14 = [(x,)]
    del x
    return __extracted_func_14(_towel_arguments_14[0][0], _towel_arguments_14.pop())


def context_mgr_b(y):
    """Test context managers."""
    _towel_arguments_14 = [(y,)]
    del y
    return __extracted_func_14(_towel_arguments_14[0][0], _towel_arguments_14.pop())


# =============================================================================
# 22. WALRUS OPERATOR (NAMED EXPRESSIONS)
# =============================================================================


def walrus_a(data):
    """Test walrus operator."""
    _towel_arguments_8 = [(data,)]
    del data
    return __extracted_func_8(_towel_arguments_8[0][0], _towel_arguments_8.pop())


def walrus_b(items):
    """Test walrus operator."""
    _towel_arguments_8 = [(items,)]
    del items
    return __extracted_func_8(_towel_arguments_8[0][0], _towel_arguments_8.pop())


# =============================================================================
# 23. CHAINED COMPARISONS
# =============================================================================


def chained_comp_a(x, y, z):
    """Test chained comparisons."""
    _towel_arguments_15 = [(z, y, x)]
    del x
    del y
    del z
    return __extracted_func_15(_towel_arguments_15[0][2], _towel_arguments_15[0][1], _towel_arguments_15[0][0], _towel_arguments_15.pop())


def chained_comp_b(a, b, c):
    """Test chained comparisons."""
    _towel_arguments_15 = [(c, b, a)]
    del a
    del b
    del c
    return __extracted_func_15(_towel_arguments_15[0][2], _towel_arguments_15[0][1], _towel_arguments_15[0][0], _towel_arguments_15.pop())


# =============================================================================
# 24. BOOLEAN SHORT-CIRCUIT
# =============================================================================


def short_circuit_a(x, y):
    """Test boolean short-circuit evaluation."""
    _towel_arguments_23 = [(y, x)]
    del x
    del y
    return __extracted_func_23(_towel_arguments_23[0][1], _towel_arguments_23[0][0], _towel_arguments_23.pop())


def short_circuit_b(a, b):
    """Test boolean short-circuit evaluation."""
    _towel_arguments_23 = [(b, a)]
    del a
    del b
    return __extracted_func_23(_towel_arguments_23[0][1], _towel_arguments_23[0][0], _towel_arguments_23.pop())


# =============================================================================
# 25. NESTED DATA STRUCTURES
# =============================================================================


def nested_a(data):
    """Test nested data structure access."""
    _towel_arguments_16 = [(data,)]
    del data
    return __extracted_func_16(_towel_arguments_16[0][0], _towel_arguments_16.pop())


def nested_b(items):
    """Test nested data structure access."""
    _towel_arguments_16 = [(items,)]
    del items
    return __extracted_func_16(_towel_arguments_16[0][0], _towel_arguments_16.pop())


# =============================================================================
# 26. MULTI-TARGET ASSIGNMENT
# =============================================================================


def multi_assign_a(x, y):
    """Test multi-target assignment."""
    _towel_arguments_10 = [(y, x)]
    del x
    del y
    return __extracted_func_10(_towel_arguments_10[0][1], _towel_arguments_10[0][0], _towel_arguments_10.pop())


def multi_assign_b(m, n):
    """Test multi-target assignment."""
    _towel_arguments_10 = [(n, m)]
    del m
    del n
    return __extracted_func_10(_towel_arguments_10[0][1], _towel_arguments_10[0][0], _towel_arguments_10.pop())


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
    _towel_arguments_17 = [(data, x)]
    del x
    del data
    return __extracted_func_17(_towel_arguments_17[0][1], _towel_arguments_17[0][0], _towel_arguments_17.pop())


def membership_b(y, items):
    """Test membership operators."""
    _towel_arguments_17 = [(items, y)]
    del y
    del items
    return __extracted_func_17(_towel_arguments_17[0][1], _towel_arguments_17[0][0], _towel_arguments_17.pop())


# =============================================================================
# 29. IDENTITY TESTS
# =============================================================================


def identity_a(x, y):
    """Test identity operators."""
    _towel_arguments_18 = [(y, x)]
    del x
    del y
    return __extracted_func_18(_towel_arguments_18[0][1], _towel_arguments_18[0][0], _towel_arguments_18.pop())


def identity_b(a, b):
    """Test identity operators."""
    _towel_arguments_18 = [(b, a)]
    del a
    del b
    return __extracted_func_18(_towel_arguments_18[0][1], _towel_arguments_18[0][0], _towel_arguments_18.pop())


# =============================================================================
# 30. COMPLEX EXPRESSIONS
# =============================================================================


def complex_expr_a(x, y, data):
    """Test complex nested expressions."""
    result = (x + y) * 2 + sum([i * 2 for i in data if i > x])
    result += len([i for i in data if x < i < y])
    result += sum(map(lambda i: i**2, filter(lambda i: i > 0, data)))
    return result


def complex_expr_b(a, b, items):
    """Test complex nested expressions."""
    output = (a + b) * 2 + sum([i * 2 for i in items if i > a])
    output += len([i for i in items if a < i < b])
    output += sum(map(lambda i: i**2, filter(lambda i: i > 0, items)))
    return output
