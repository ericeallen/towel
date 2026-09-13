# Copyright notice comes first here as well.
"""Module b.

Usage notes:
import this module to get fb, or
from pkg.b import fb
"""
def fb(items):
    out = []
    for i in items:
        out.append(i + 1)
    out.append("b")
    return out
