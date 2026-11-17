"""
Example 1: Simple repeated code in functions.

Tests: Same-file duplicate detection in simple functions.
"""


def __extracted_func_0(__param_0, __param_1, __param_2, __param_3, _towel_owner):
    user = {"id": __param_0, "name": __param_1, "email": __param_2}
    # Validate user data (DUPLICATE!)
    # Validate admin data (DUPLICATE!)
    # Validate guest data (DUPLICATE!)
    if not user.get('id'):
        raise ValueError('User ID is required')
    if not user.get('name'):
        raise ValueError('User name is required')
    if len(user.get('name', '')) < 2:
        raise ValueError('User name too short')

    print(f"Processing {__param_3}: {user['name']}")
    return user


def process_user_data(user_id):
    """Process user data."""
    # Fetch user
    _towel_arguments = [(user_id,)]
    del user_id
    return __extracted_func_0(_towel_arguments[0][0], "John", "john@example.com", 'user', _towel_arguments.pop())


def process_admin_data(admin_id):
    """Process admin data."""
    # Fetch admin
    _towel_arguments = [(admin_id,)]
    del admin_id
    return __extracted_func_0(_towel_arguments[0][0], "Admin", "admin@example.com", 'admin', _towel_arguments.pop())


def process_guest_data(guest_id):
    """Process guest data."""
    # Fetch guest
    _towel_arguments = [(guest_id,)]
    del guest_id
    return __extracted_func_0(_towel_arguments[0][0], "Guest", "guest@example.com", 'guest', _towel_arguments.pop())
