import weakref
class T:
    pass
def id1():
    t = T()
    r = weakref.ref(t)
    alive = r() is t
    ident = id(t) == id(r())
    return alive, ident, r() is not None
def id2():
    t = T()
    r = weakref.ref(t)
    alive = r() is t
    ident = id(t) == id(r())
    return alive, ident, r() is not None, 2
def id3():
    t = T()
    r = weakref.ref(t)
    del t
    return r() is None
def id4():
    t = T()
    r = weakref.ref(t)
    del t
    return r() is None, 4
if __name__ == "__main__":
    print(id1(), id2(), id3(), id4())
