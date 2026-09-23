# The frame handle is taken before the shared block, in the same loop body, and
# read after it: ``f_locals`` lists the block's names. Moved into a helper,
# those names are the helper's, and the list comes back shorter. The check for
# frame reads outside a block stopped walking the loop at the block and never
# saw the handle.
import sys


def first(items):
    names = []
    for item in items:
        frame = sys._getframe()
        doubled = item * 2
        label = "first" + str(doubled)
        print(label, len(label))
        names.append(sorted(frame.f_locals))
    return names


def second(items):
    names = []
    for item in items:
        frame = sys._getframe()
        doubled = item * 2
        label = "second" + str(doubled)
        print(label, len(label))
        names.append(sorted(frame.f_locals))
    return names


if __name__ == "__main__":
    print(first([1]), second([3]))
