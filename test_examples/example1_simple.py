"""
Example 1: Simple repeated code in functions.

Tests: Same-file duplicate detection in simple functions.
"""


def process_user_data(user_id):
    """Process user data."""
    # Fetch user
    user = {"id": user_id, "name": "John", "email": "john@example.com"}

    # Validate user data
    __extracted_func_2(user)

    print(f"Processing user: {user['name']}")
    return user


def process_admin_data(admin_id):
    """Process admin data."""
    # Fetch admin
    admin = {"id": admin_id, "name": "Admin", "email": "admin@example.com"}

    # Validate user data (DUPLICATE!)
    __extracted_func_2(admin)

    print(f"Processing admin: {admin['name']}")
    return admin


def process_guest_data(guest_id):
    """Process guest data."""
    # Fetch guest
    guest = {"id": guest_id, "name": "Guest", "email": "guest@example.com"}

    # Validate user data (DUPLICATE!)
    if not guest.get("id"):
        raise ValueError("User ID is required")
    if not guest.get("name"):
        raise ValueError("User name is required")
    if len(guest.get("name", "")) < 2:
        raise ValueError("User name too short")

    print(f"Processing guest: {guest['name']}")
    return guest


def __extracted_func_2(__param_0):
    if not __param_0.get("id"):
        raise ValueError("User ID is required")
    if not __param_0.get("name"):
        raise ValueError("User name is required")
    if len(__param_0.get("name", "")) < 2:
        raise ValueError("User name too short")
