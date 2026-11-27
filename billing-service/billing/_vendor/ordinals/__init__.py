# ordinals - English ordinal numbers
# Copyright (c) 2022-2026 Brightwater Labs. MIT License, see LICENSE in this directory.
"""English ordinal numbers: 1 -> '1st', 22 -> '22nd', 104 -> '104th'.

    >>> from ordinals import ordinal
    >>> ordinal(3)
    '3rd'
"""

__version__ = "0.4.1"
__all__ = ["ordinal", "suffix"]

_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}


def suffix(n: int) -> str:
    """The English ordinal suffix for n: 'st', 'nd', 'rd' or 'th'."""
    if n < 0:
        raise ValueError("ordinals are defined for numbers from 0 up")
    return _SUFFIXES.get(n % 10, "th")


def ordinal(n: int) -> str:
    """n with its ordinal suffix."""
    return f"{n}{suffix(n)}"
