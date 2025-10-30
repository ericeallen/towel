"""
Comprehensive syntactic coverage test.

Tests every Python syntactic construct that can appear in code blocks,
ensuring the refactoring system handles all edge cases correctly.
"""

# =============================================================================
# 1. LITERALS AND BASIC DATA STRUCTURES
# =============================================================================

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
    output = y + 42  # int
    output = output + 3.14  # float
    output = output + 1j  # complex
    output = output + len("string")  # string
    output = output + len(b"bytes")  # bytes
    output = output + (1 if True else 0)  # boolean
    output = output + (0 if None else 1)  # None
    return output

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
    info = [y, y + 1, y + 2]  # list
    info = info + list((y, y + 1))  # tuple
    info = info + list({y, y + 1})  # set
    info = info + list({"a": y, "b": y + 1}.values())  # dict
    return sum(info)

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
    result = result ** 2  # exponentiation
    return result

def arithmetic_b(a, b):
    """Test all arithmetic operators."""
    output = a + b  # addition
    output = output - b  # subtraction
    output = output * 2  # multiplication
    output = output / 2  # division
    output = output // 2  # floor division
    output = output % 3  # modulo
    output = output ** 2  # exponentiation
    return output

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
    output = 1 if a == b else 0
    output += 1 if a != b else 0
    output += 1 if a < b else 0
    output += 1 if a > b else 0
    output += 1 if a <= b else 0
    output += 1 if a >= b else 0
    output += 1 if a is b else 0
    output += 1 if a is not b else 0
    return output

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
    output = a and b
    output = output or b
    output = not output
    return output

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
    output = a & b  # and
    output = output | b  # or
    output = output ^ b  # xor
    output = ~output  # not
    output = output << 1  # left shift
    output = output >> 1  # right shift
    return output

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
    output = y
    output += 10
    output -= 5
    output *= 2
    output /= 2
    output //= 2
    output %= 3
    output **= 2
    return output

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
    result = data[0]  # simple subscript
    result += data[-1]  # negative index
    result += sum(data[1:3])  # slice
    result += sum(data[::2])  # step slice
    result += sum(data[::-1])  # reverse
    return result

def subscript_b(items):
    """Test subscript and slice operations."""
    output = items[0]  # simple subscript
    output += items[-1]  # negative index
    output += sum(items[1:3])  # slice
    output += sum(items[::2])  # step slice
    output += sum(items[::-1])  # reverse
    return output

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
    output = thing.value
    output += thing.data[0]
    output += thing.get_value()
    return output

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
    output = helper(a, b)  # positional
    output += helper(a, b, 20)  # with optional
    output += helper(a, b, c=30)  # keyword
    output += helper(a, b, 40, 50)  # *args
    output += helper(a, b, d=60)  # **kwargs
    return output

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
    result = sum({x * 2 for x in data})
    result += sum({x for x in data if x > 0})
    return result

def set_comp_b(items):
    """Test set comprehensions."""
    output = sum({x * 2 for x in items})
    output += sum({x for x in items if x > 0})
    return output

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
    output = sum({i: x * 2 for i, x in enumerate(items)}.values())
    output += sum({i: x for i, x in enumerate(items) if x > 0}.values())
    return output

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
    output = sum(x * 2 for x in items)
    output += sum(x for x in items if x > 0)
    return output

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
    mapper = lambda x: x * 2
    output = sum(map(mapper, items))
    output += sum(map(lambda x: x + 1, items))
    return output

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
    output = y * 2 if y > limit else y
    output = output + 10 if output < 100 else output - 10
    return output

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
    result = x
    return __extracted_func_413(x, result)


def exceptions_b(y):
    """Test exception handling."""
    output = y
    return __extracted_func_413(y, output)

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
    output = y
    with DummyContext(10) as value:
        output += value
    return output

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
    output = 0
    if (n := len(items)) > 5:
        output += n
    if (total := sum(items)) > 10:
        output += total
    return output

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
    output = 1 if a < b < c else 0
    output += 1 if a <= b <= c else 0
    output += 1 if a == b == c else 0
    return output

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
    output = a and b or a
    output = output and (a + b) or (a - b)
    return output

# =============================================================================
# 25. NESTED DATA STRUCTURES
# =============================================================================

def nested_a(data):
    """Test nested data structure access."""
    result = data[0][0]
    result += data[1]["key"]
    result += data[2][0][1]
    return result

def nested_b(items):
    """Test nested data structure access."""
    output = items[0][0]
    output += items[1]["key"]
    output += items[2][0][1]
    return output

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
    output = sum_val = m + n
    output += sum_val
    p = q = r = m
    output += p + q + r
    return output

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
    result = 1 if x in data else 0
    result += 1 if x not in data else 0
    result += 1 if "key" in {"key": x} else 0
    return result

def membership_b(y, items):
    """Test membership operators."""
    output = 1 if y in items else 0
    output += 1 if y not in items else 0
    output += 1 if "key" in {"key": y} else 0
    return output

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
    output = 1 if a is None else 0
    output += 1 if a is not None else 0
    output += 1 if a is b else 0
    return output

# =============================================================================
# 30. COMPLEX EXPRESSIONS
# =============================================================================

def complex_expr_a(x, y, data):
    """Test complex nested expressions."""
    result = (x + y) * 2 + sum([i * 2 for i in data if i > x])
    result += len([i for i in data if x < i < y])
    result += sum(map(lambda i: i ** 2, filter(lambda i: i > 0, data)))
    return result

def complex_expr_b(a, b, items):
    """Test complex nested expressions."""
    output = (a + b) * 2 + sum([i * 2 for i in items if i > a])
    output += len([i for i in items if a < i < b])
    output += sum(map(lambda i: i ** 2, filter(lambda i: i > 0, items)))
    return output


def __extracted_func_413(__param_688, result):
    try:
        result = result / __param_688
    except ZeroDivisionError:
        result = 0
    except Exception as e:
        result = -1
    else:
        result += 10
    finally:
        result += 1
    return result


