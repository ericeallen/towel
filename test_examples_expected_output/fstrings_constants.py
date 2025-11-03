"""
Test cases for f-strings and constant parameterization.

Tests that:
1. F-string literal parts are never parameterized
2. F-string expressions can be parameterized
3. Constants can be parameterized when enabled
"""


def log_user(user_id, name):
    """Log user with f-string."""
    status = "active"
    count = 1
    message = f"User {name} (ID: {user_id}) is {status}"
    print(message)
    return count


def log_admin(admin_id, name):
    """Log admin with f-string (different literal text - should NOT unify)."""
    status = "active"
    count = 1
    message = f"Admin {name} (ID: {admin_id}) is {status}"
    print(message)
    return count


def format_number_a(value):
    """F-string with formatting."""
    precision = 2
    result = f"Value: {value:.{precision}f}"
    return result


def format_number_b(value):
    """F-string with formatting (same literal - should unify)."""
    precision = 2
    result = f"Value: {value:.{precision}f}"
    return result


def const_parameterization_a(item):
    """Test constant parameterization."""
    return __extracted_func_453(100, 2, item)


def const_parameterization_b(item):
    """Test constant parameterization (different constants)."""
    return __extracted_func_453(200, 3, item)


def string_const_a(name):
    """String constants."""
    prefix = "Mr. "
    suffix = " Esq."
    return prefix + name + suffix


def string_const_b(name):
    """String constants (different values)."""
    prefix = "Dr. "
    suffix = " PhD"
    return prefix + name + suffix


def mixed_fstring_a(x, y):
    """Mixed f-string and regular strings."""
    result = f"Processing {x}" + " and " + f"also {y}"
    return result


def mixed_fstring_b(x, y):
    """Mixed f-string and regular strings (same structure)."""
    result = f"Processing {x}" + " and " + f"also {y}"
    return result


def __extracted_func_453(__param_0, __param_1, item):
    threshold = __param_0
    multiplier = __param_1
    if item > threshold:
        return item * multiplier
    return 0


