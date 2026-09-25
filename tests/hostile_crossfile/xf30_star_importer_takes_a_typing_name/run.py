from pkg import b
from pkg.a import Config, f1, f2

print(f1([1, 2], Config()), f2([3], Config()))
print(b.kind(len), b.kind(3), b.label())
