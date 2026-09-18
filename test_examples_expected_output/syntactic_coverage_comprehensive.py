"""
Comprehensive syntactic coverage test.

Tests every Python syntactic construct that can appear in code blocks,
ensuring the refactoring system handles all edge cases correctly.
"""

# =============================================================================
# 1. LITERALS AND BASIC DATA STRUCTURES
# =============================================================================


def __extracted_func_3(__param_0, __param_1):
    result = len(f'{__param_0 + __param_1}')
    result += len(f'{__param_0 * 2:04d}')
    result += len(f'{__param_0:.2f}')
    return result


def __extracted_func_2(__param_0, __param_1, __param_2, data):
    result = __param_0
    result += __param_1
    result += __param_2()
    return result


def __extracted_func_1(__param_0, __param_1, __param_2, __param_3, s1):
    s2 = __param_0 % (__param_1, __param_2)
    s3 = __param_3.format(__param_1, __param_2)
    result = len(s1) + len(s2) + len(s3)
    return result


def __extracted_func_0(__param_0):
    x, y = __param_0[:2]
    result = x + y
    a, b, c = __param_0[:3]
    result += a + b + c
    return result


def literals_a(x):
    """Test all literal types."""
    result = x + 42  # int
    result = result + 3.14  # float
    result = result + 1j  # complex
    result = result + len("string")  # string
    result = result + len(b"bytes")  # bytes
    result = result + (1 if True else 0)  # boolean
    result = result + (0 if None else 1)  # None
    return result


def literals_b(y):
    """Test all literal types."""
    return literals_a(y)


# =============================================================================
# 2. COLLECTION LITERALS
# =============================================================================


def collections_a(x):
    """Test collection literal construction."""
    data = [x, x + 1, x + 2]  # list
    data = data + list((x, x + 1))  # tuple
    data = data + list({x, x + 1})  # set
    data = data + list({"a": x, "b": x + 1}.values())  # dict
    return sum(data)


def collections_b(y):
    """Test collection literal construction."""
    return collections_a(y)


# =============================================================================
# 3. ARITHMETIC OPERATIONS
# =============================================================================


def arithmetic_a(x, y):
    """Test all arithmetic operators."""
    result = x + y  # addition
    result = result - y  # subtraction
    result = result * 2  # multiplication
    result = result / 2  # division
    result = result // 2  # floor division
    result = result % 3  # modulo
    result = result**2  # exponentiation
    return result


def arithmetic_b(a, b):
    """Test all arithmetic operators."""
    return arithmetic_a(a, b)


# =============================================================================
# 4. COMPARISON OPERATIONS
# =============================================================================


def comparisons_a(x, y):
    """Test all comparison operators."""
    result = 1 if x == y else 0
    result += 1 if x != y else 0
    result += 1 if x < y else 0
    result += 1 if x > y else 0
    result += 1 if x <= y else 0
    result += 1 if x >= y else 0
    result += 1 if x is y else 0
    result += 1 if x is not y else 0
    return result


def comparisons_b(a, b):
    """Test all comparison operators."""
    return comparisons_a(a, b)


# =============================================================================
# 5. LOGICAL OPERATIONS
# =============================================================================


def logical_a(x, y):
    """Test logical operators."""
    result = x and y
    result = result or y
    result = not result
    return result


def logical_b(a, b):
    """Test logical operators."""
    return logical_a(a, b)


# =============================================================================
# 6. BITWISE OPERATIONS
# =============================================================================


def bitwise_a(x, y):
    """Test bitwise operators."""
    result = x & y  # and
    result = result | y  # or
    result = result ^ y  # xor
    result = ~result  # not
    result = result << 1  # left shift
    result = result >> 1  # right shift
    return result


def bitwise_b(a, b):
    """Test bitwise operators."""
    return bitwise_a(a, b)


# =============================================================================
# 7. AUGMENTED ASSIGNMENTS
# =============================================================================


def augmented_a(x):
    """Test augmented assignment operators."""
    result = x
    result += 10
    result -= 5
    result *= 2
    result /= 2
    result //= 2
    result %= 3
    result **= 2
    return result


def augmented_b(y):
    """Test augmented assignment operators."""
    return augmented_a(y)


# =============================================================================
# 8. SEQUENCE UNPACKING
# =============================================================================


def unpacking_a(data):
    """Test various unpacking patterns."""
    result = __extracted_func_0(data)
    first, *rest = data
    result += first + sum(rest)
    return result


def unpacking_b(items):
    """Test various unpacking patterns."""
    output = __extracted_func_0(items)
    head, *tail = items
    output += head + sum(tail)
    return output


# =============================================================================
# 9. SUBSCRIPT AND SLICING
# =============================================================================


def subscript_a(data):
    """Test subscript and slice operations."""
    result = data[0]  # simple subscript
    result += data[-1]  # negative index
    result += sum(data[1:3])  # slice
    result += sum(data[::2])  # step slice
    result += sum(data[::-1])  # reverse
    return result


def subscript_b(items):
    """Test subscript and slice operations."""
    return subscript_a(items)


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
    result = obj.value
    result += obj.data[0]
    result += obj.get_value()
    return result


def attributes_b(thing):
    """Test attribute access."""
    return attributes_a(thing)


# =============================================================================
# 11. FUNCTION CALLS
# =============================================================================


def helper(a, b, c=10, *args, **kwargs):
    return a + b + c + sum(args) + sum(kwargs.values())


def calls_a(x, y):
    """Test various function call patterns."""
    result = helper(x, y)  # positional
    result += helper(x, y, 20)  # with optional
    result += helper(x, y, c=30)  # keyword
    result += helper(x, y, 40, 50)  # *args
    result += helper(x, y, d=60)  # **kwargs
    return result


def calls_b(a, b):
    """Test various function call patterns."""
    return calls_a(a, b)


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
    return list_comp_a(items)


# =============================================================================
# 13. SET COMPREHENSIONS
# =============================================================================


def set_comp_a(data):
    """Test set comprehensions."""
    result = sum({x * 2 for x in data})
    result += sum({x for x in data if x > 0})
    return result


def set_comp_b(items):
    """Test set comprehensions."""
    return set_comp_a(items)


# =============================================================================
# 14. DICT COMPREHENSIONS
# =============================================================================


def dict_comp_a(data):
    """Test dict comprehensions."""
    result = sum({i: x * 2 for i, x in enumerate(data)}.values())
    result += sum({i: x for i, x in enumerate(data) if x > 0}.values())
    return result


def dict_comp_b(items):
    """Test dict comprehensions."""
    return dict_comp_a(items)


# =============================================================================
# 15. GENERATOR EXPRESSIONS
# =============================================================================


def generator_a(data):
    """Test generator expressions."""
    result = sum(x * 2 for x in data)
    result += sum(x for x in data if x > 0)
    return result


def generator_b(items):
    """Test generator expressions."""
    return generator_a(items)


# =============================================================================
# 16. LAMBDA EXPRESSIONS
# =============================================================================


def lambda_a(data):
    """Test lambda expressions."""
    mapper = lambda x: x * 2
    result = sum(map(mapper, data))
    result += sum(map(lambda x: x + 1, data))
    return result


def lambda_b(items):
    """Test lambda expressions."""
    return lambda_a(items)


# =============================================================================
# 17. CONDITIONAL EXPRESSIONS (TERNARY)
# =============================================================================


def ternary_a(x, threshold):
    """Test conditional expressions."""
    result = x * 2 if x > threshold else x
    result = result + 10 if result < 100 else result - 10
    return result


def ternary_b(y, limit):
    """Test conditional expressions."""
    return ternary_a(y, limit)


# =============================================================================
# 18. STRING FORMATTING
# =============================================================================


def string_fmt_a(x, y):
    """Test string formatting."""
    s1 = f"x={x}, y={y}"
    return __extracted_func_1('x=%d, y=%d', x, y, 'x={}, y={}', s1)


def string_fmt_b(a, b):
    """Test string formatting."""
    s1 = f"a={a}, b={b}"
    return __extracted_func_1('a=%d, b=%d', a, b, 'a={}, b={}', s1)


# =============================================================================
# 19. F-STRING EXPRESSIONS
# =============================================================================


def fstring_a(x, y):
    """Test f-string with expressions."""
    result = __extracted_func_3(x, y)
    result += len(f"{x=}")
    return result


def fstring_b(a, b):
    """Test f-string with expressions."""
    output = __extracted_func_3(a, b)
    output += len(f"{a=}")
    return output


# =============================================================================
# 20. EXCEPTION HANDLING
# =============================================================================


def exceptions_a(x):
    """Test exception handling."""
    result = x
    try:
        result = result / x
    except ZeroDivisionError:
        result = 0
    except Exception as e:
        result = -1
    else:
        result += 10
    finally:
        result += 1
    return result


def exceptions_b(y):
    """Test exception handling."""
    return exceptions_a(y)


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
    result = x
    with DummyContext(10) as value:
        result += value
    return result


def context_mgr_b(y):
    """Test context managers."""
    return context_mgr_a(y)


# =============================================================================
# 22. WALRUS OPERATOR (NAMED EXPRESSIONS)
# =============================================================================


def walrus_a(data):
    """Test walrus operator."""
    result = 0
    if (n := len(data)) > 5:
        result += n
    if (total := sum(data)) > 10:
        result += total
    return result


def walrus_b(items):
    """Test walrus operator."""
    return walrus_a(items)


# =============================================================================
# 23. CHAINED COMPARISONS
# =============================================================================


def chained_comp_a(x, y, z):
    """Test chained comparisons."""
    result = 1 if x < y < z else 0
    result += 1 if x <= y <= z else 0
    result += 1 if x == y == z else 0
    return result


def chained_comp_b(a, b, c):
    """Test chained comparisons."""
    return chained_comp_a(a, b, c)


# =============================================================================
# 24. BOOLEAN SHORT-CIRCUIT
# =============================================================================


def short_circuit_a(x, y):
    """Test boolean short-circuit evaluation."""
    result = x and y or x
    result = result and (x + y) or (x - y)
    return result


def short_circuit_b(a, b):
    """Test boolean short-circuit evaluation."""
    return short_circuit_a(a, b)


# =============================================================================
# 25. NESTED DATA STRUCTURES
# =============================================================================


def nested_a(data):
    """Test nested data structure access."""
    return __extracted_func_2(data[0][0], data[1]['key'], lambda: data[2][0][1], data)


def nested_b(items):
    """Test nested data structure access."""
    return nested_a(items)


# =============================================================================
# 26. MULTI-TARGET ASSIGNMENT
# =============================================================================


def multi_assign_a(x, y):
    """Test multi-target assignment."""
    result = total = x + y
    result += total
    a = b = c = x
    result += a + b + c
    return result


def multi_assign_b(m, n):
    """Test multi-target assignment."""
    return multi_assign_a(m, n)


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
    return __extracted_func_2(1 if x in data else 0, 1 if x not in data else 0, lambda: 1 if 'key' in {'key': x} else 0, data)


def membership_b(y, items):
    """Test membership operators."""
    return membership_a(y, items)


# =============================================================================
# 29. IDENTITY TESTS
# =============================================================================


def identity_a(x, y):
    """Test identity operators."""
    result = 1 if x is None else 0
    result += 1 if x is not None else 0
    result += 1 if x is y else 0
    return result


def identity_b(a, b):
    """Test identity operators."""
    return identity_a(a, b)


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
    return complex_expr_a(a, b, items)
