"""Run shop as its wheel ships it, without _devtools.py, then _devtools from the source tree."""
import os
import shutil
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
source = os.path.join(here, "pkg", "src")
with tempfile.TemporaryDirectory() as shipped:
    shutil.copytree(
        os.path.join(source, "shop"),
        os.path.join(shipped, "shop"),
        ignore=shutil.ignore_patterns("_devtools.py", "__pycache__"),
    )
    sys.path.insert(0, shipped)
    from shop.stats import describe

    print(describe([3, 1, 2]))
    for name in [name for name in sys.modules if name == "shop" or name.startswith("shop.")]:
        del sys.modules[name]
    sys.path[0] = source
    from shop._devtools import dump

    print(dump([5, 4]))
