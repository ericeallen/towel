def register_user(form):
    user = dict(form)
    if not user.get("name"):
        raise ValueError("name is required")
    if len(user["name"]) < 2:
        raise ValueError("name is too short")
    return save(user)


def register_admin(form):
    admin = dict(form, role="admin")
    if not admin.get("name"):
        raise ValueError("name is required")
    if len(admin["name"]) < 8:
        raise ValueError("name is too short")
    return save(admin)
