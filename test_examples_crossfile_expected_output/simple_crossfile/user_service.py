"""
User service module.
"""
from admin_service import __extracted_func_3490


def validate_user_email(email):
    """Validate user email format."""
    # Validation logic (DUPLICATE across files!)
    return __extracted_func_3490(email)


def create_user(name, email):
    """Create a new user."""
    if not validate_user_email(email):
        return None
    return {"name": name, "email": email, "active": True}
