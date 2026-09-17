"""
Example 1: Simple repeated code in functions.

Tests: Same-file duplicate detection in simple functions.
"""


def __extracted_func_1(__param_0):
    if not __param_0.get('id'):
        raise ValueError('User ID is required')
    if not __param_0.get('name'):
        raise ValueError('User name is required')
    if len(__param_0.get('name', '')) < 2:
        raise ValueError('User name too short')


def __extracted_func_0(__param_0, __param_1, __param_2):
    user = {'id': __param_0, 'name': __param_1, 'email': __param_2}
    __extracted_func_1(user)
    return user


def process_user_data(user_id):
    """Process user data."""
    # Fetch user
    user = __extracted_func_0(user_id, 'John', 'john@example.com')

    print(f"Processing user: {user['name']}")
    return user


def process_admin_data(admin_id):
    """Process admin data."""
    # Fetch admin
    admin = __extracted_func_0(admin_id, 'Admin', 'admin@example.com')

    print(f"Processing admin: {admin['name']}")
    return admin


def process_guest_data(guest_id):
    """Process guest data."""
    # Fetch guest
    guest = {"id": guest_id, "name": "Guest", "email": "guest@example.com"}

    # Validate guest data (DUPLICATE!)
    __extracted_func_1(guest)

    print(f"Processing guest: {guest['name']}")
    return guest
