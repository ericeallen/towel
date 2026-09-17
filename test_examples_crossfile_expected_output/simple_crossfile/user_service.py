"""
User service module.
"""
from admin_service import validate_admin_email


def validate_user_email(email):
    """Validate user email format."""
    # Validation logic (DUPLICATE across files!)
    return validate_admin_email(email)


def create_user(name, email):
    """Create a new user."""
    if not validate_user_email(email):
        return None
    return {"name": name, "email": email, "active": True}
