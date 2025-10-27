"""
Example 1: Simple repeated code in functions.

Tests: Same-file duplicate detection in simple functions.
"""


def process_user_data(user_id):
    """Process user data."""
    # Fetch user
    user = {"id": user_id, "name": "John", "email": "john@example.com"}

    # Validate user data
    if not user.get("id"):
        raise ValueError("User ID is required")
    if not user.get("name"):
        raise ValueError("User name is required")
    if len(user.get("name", "")) < 2:
        raise ValueError("User name too short")

    print(f"Processing user: {user['name']}")
    return user


def process_admin_data(admin_id):
    """Process admin data."""
    # Fetch admin
    admin = {"id": admin_id, "name": "Admin", "email": "admin@example.com"}

    # Validate user data (DUPLICATE!)
    if not admin.get("id"):
        raise ValueError("User ID is required")
    if not admin.get("name"):
        raise ValueError("User name is required")
    if len(admin.get("name", "")) < 2:
        raise ValueError("User name too short")

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
