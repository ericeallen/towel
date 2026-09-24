"""Run zzapp as it ships: its own package alone, beside the installed zzlib library."""
import os
import shutil
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
with tempfile.TemporaryDirectory() as shipped:
    shutil.copytree(os.path.join(here, "pkg", "zzapp"), os.path.join(shipped, "zzapp"))
    sys.path[:0] = [shipped, os.path.join(here, "site-packages")]
    import zzapp.core

    print("use(2) ->", zzapp.core.use(2))
