def lookup(table, name):
    return table.get(name), [name] * 2
def first(table, name, log):
    from json import dumps
    frame, stmts = lookup(table, name)
    if not stmts:
        raise KeyError(name)
    log.append(dumps(frame))
    return frame, stmts
def second(table, name, log):
    from json import dumps
    frame, stmts = lookup(table, name)
    if not stmts:
        raise KeyError(name)
    log.extend([dumps(s) for s in stmts])
    return stmts, frame
if __name__ == "__main__":
    log = []
    print(first({"a": 1}, "a", log), second({"b": 2}, "b", log), log)
