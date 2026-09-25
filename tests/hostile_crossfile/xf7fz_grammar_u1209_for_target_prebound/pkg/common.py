LOG = []
G = 5


def tr(tag, v):
    print("tr", tag, repr(v))
    LOG.append(tag)
    return v


class Box:
    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return f"Box({self.v!r})"

    def __eq__(self, other):
        return isinstance(other, Box) and self.v == other.v


class Ctx:
    def __init__(self, tag):
        self.tag = tag

    def __enter__(self):
        print("enter", self.tag)
        return self.tag

    def __exit__(self, et, ev, tb):
        print("exit", self.tag, et.__name__ if et else None)
        return False


def bump():
    global G
    G += 1
    return G
