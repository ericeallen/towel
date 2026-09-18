"""
Example 1: Simple repeated code in functions.

Tests: Same-file duplicate detection in simple functions.
"""


def __extracted_func_0(__param_0, __param_1, __param_2):
    user = {'id': __param_0, 'name': __param_1, 'email': __param_2}
    if not user.get('id'):
        raise ValueError('User ID is required')
    if not user.get('name'):
        raise ValueError('User name is required')
    if len(user.get('name', '')) < 2:
        raise ValueError('User name too short')
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
    guest = __extracted_func_0(guest_id, 'Guest', 'guest@example.com')

    print(f"Processing guest: {guest['name']}")
    return guest
