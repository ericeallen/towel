"""Run shop as its wheel ships it, without shop.devtools, then the developer command from the tree."""
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
        ignore=shutil.ignore_patterns("devtools", "__pycache__"),
    )
    sys.path.insert(0, shipped)
    from shop.stats import describe
    from shop.cli import main

    print(describe([3, 1, 2]), main([]))
    for name in [name for name in sys.modules if name == "shop" or name.startswith("shop.")]:
        del sys.modules[name]
    sys.path[0] = source
    from shop.cli import main

    print(main(["dev"]))
