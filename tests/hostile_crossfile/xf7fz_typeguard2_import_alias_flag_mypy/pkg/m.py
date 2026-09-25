import pkg
from pkg.f import make
from .compat import DEBUG as TYPE_CHECKING



def f1(k: int) -> int:
    x = make()
    if k > 100:
        return 0
    x.poke(k)
    n = x.size + k
    print("f1", n)
    return n


def f2(k: int) -> int:
    x = make()
    k = k * 2
    x.poke(k)
    n = x.size + k
    print("f2", n)
    return n * 2


if TYPE_CHECKING:
    MODE = 'alias-flag-on'
