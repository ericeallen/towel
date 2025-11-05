"""
User service module.
"""


def validate_user_email(email):
    """Validate user email format."""
    # Validation logic (DUPLICATE across files!)
    if not email:
        return False
    if "@" not in email:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    if not parts[0] or not parts[1]:
        return False
    return True


def create_user(name, email):
    """Create a new user."""
    if not validate_user_email(email):
        return None
    return {"name": name, "email": email, "active": True}
