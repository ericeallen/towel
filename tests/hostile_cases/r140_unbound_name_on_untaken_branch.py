# A name nothing binds, read only on a branch no run takes. Hoisting it into
# an eager argument raises NameError at every call; the thunk keeps the read
# where the block had it.
def f(*args):
    return sum(args)
def site_one(src, limit, alpha):
    if limit < 0:
        v = zeta
    v = alpha
    v = 0
    f(src)
    return v + 1
def site_two(src, limit, beta):
    if limit < 0:
        v = zeta
    v = beta
    v = 0
    f(src)
    return v - 1
if __name__ == "__main__":
    print(site_one(1, 2, 3), site_two(4, 5, 6))
