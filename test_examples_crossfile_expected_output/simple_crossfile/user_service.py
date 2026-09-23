"""
User service module.
"""

import admin_service
from admin_service import __extracted_func_0


def validate_user_email(email):
    """Validate user email format."""
    # Validation logic (DUPLICATE across files!)
    return __extracted_func_0(email)


def create_user(name, email):
    """Create a new user."""
    if not validate_user_email(email):
        return None
    return {"name": name, "email": email, "active": True}
