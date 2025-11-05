"""
Admin service module.
"""


def validate_admin_email(email):
    """Validate admin email format."""
    # Validation logic (DUPLICATE across files!)
    return __extracted_func_3490(email)


def create_admin(name, email, permissions):
    """Create a new admin user."""
    if not validate_admin_email(email):
        return None
    return {"name": name, "email": email, "active": True, "permissions": permissions}


def __extracted_func_3490(email):
    if not email:
        return False
    if '@' not in email:
        return False
    parts = email.split('@')
    if len(parts) != 2:
        return False
    if not parts[0] or not parts[1]:
        return False
    return True


