from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pkg.w import Widget


def make() -> Widget:
    from pkg.w import Widget as W

    return W()
