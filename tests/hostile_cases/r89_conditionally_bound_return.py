def from_env(name, table):
    key = table.get(name)
    if key is None:
        msg = f"unknown name: {name}"
        raise ValueError(msg)
    value = table.get(key)
    if value is None:
        msg = f"unset key: {key}"
        raise KeyError(msg)
    return value
def from_registry(name, table):
    guid = table.get(name)
    if guid is None:
        msg = f"unknown name: {name}"
        raise ValueError(msg)
    found = table.get(guid, None)
    if found is None:
        msg = f"missing guid: {guid}"
        raise LookupError(msg)
    return found.upper()
if __name__ == "__main__":
    table = {"a": "b", "b": "c"}
    print(from_env("a", table), from_registry("a", {"a": "b", "b": "c"}))
    for f, arg in ((from_env, "zz"), (from_env, "b"), (from_registry, "zz"), (from_registry, "b")):
        try:
            print(f(arg, table))
        except (ValueError, KeyError, LookupError) as exc:
            print(type(exc).__name__, exc)
