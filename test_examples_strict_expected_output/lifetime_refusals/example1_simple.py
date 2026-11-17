"""
Example 1: Simple repeated code in functions.

Tests: Same-file duplicate detection in simple functions.
"""


def __extracted_func_0(__param_0, __param_1):
    if not __param_0.get('id'):
        raise ValueError('User ID is required')
    if not __param_0.get('name'):
        raise ValueError('User name is required')
    if len(__param_0.get('name', '')) < 2:
        raise ValueError('User name too short')

    print(f"Processing {__param_1}: {__param_0['name']}")
    return __param_0


def process_user_data(user_id):
    """Process user data."""
    # Fetch user
    user = {"id": user_id, "name": "John", "email": "john@example.com"}

    # Validate user data (DUPLICATE!)
    return __extracted_func_0(user, 'user')


def process_admin_data(admin_id):
    """Process admin data."""
    # Fetch admin
    admin = {"id": admin_id, "name": "Admin", "email": "admin@example.com"}

    # Validate admin data (DUPLICATE!)
    return __extracted_func_0(admin, 'admin')


def process_guest_data(guest_id):
    """Process guest data."""
    # Fetch guest
    guest = {"id": guest_id, "name": "Guest", "email": "guest@example.com"}

    # Validate guest data (DUPLICATE!)
    return __extracted_func_0(guest, 'guest')
