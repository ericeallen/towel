"""Developer helpers; the wheel leaves this module out, so it can host nothing for stats."""


def dump(values):
    count = len(values)
    biggest = max(values)
    smallest = min(values)
    return f"dev: {count} values, {smallest}..{biggest}"
