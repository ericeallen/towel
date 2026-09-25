"""Developer helpers; setuptools' find excludes this subpackage from the wheel."""


def dump(values):
    count = len(values)
    biggest = max(values)
    smallest = min(values)
    return f"dev: {count} values, {smallest}..{biggest}"
