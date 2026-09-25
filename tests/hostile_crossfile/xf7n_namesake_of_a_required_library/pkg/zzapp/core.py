# zzlib is the distribution pyproject.toml requires, not the directory beside this package.
from zzlib.utils import echo


def use(n):
    echo(n)
    print("app")
    total = n * 3
    extra = total + 7
    print("shared", total, extra)
    return extra + 1
