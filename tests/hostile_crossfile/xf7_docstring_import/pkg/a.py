# Copyright notice comes first, so the docstring is not on line one.
"""Module a.

Usage notes:
import this module to get fa, or
from pkg.a import fa
"""
import os
def fa(items):
    out = []
    for i in items:
        out.append(i + 1)
    out.append("a")
    return out
