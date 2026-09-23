# Nothing under TYPE_CHECKING runs, however it branches, so importing this
# module still runs nothing but its definitions.
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    if sys.version_info >= (3, 11):
        from typing import Self
    else:
        from typing_extensions import Self
    print("never")


def summarize(values):
    print("summarize")
    total = 0
    for value in values:
        total += value * 2
    return total + 1
