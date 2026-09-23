import sys

import pkg.beta
from pkg.registry import PLUGINS

print("after beta:", PLUGINS, "pkg.alpha" in sys.modules)
print(pkg.beta.aggregate([1, 2]))
