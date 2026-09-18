import sys
from pkg import b
if __name__ == "__main__":
    print(b.build([1]), sorted(m for m in sys.modules if m.startswith("pkg")))
