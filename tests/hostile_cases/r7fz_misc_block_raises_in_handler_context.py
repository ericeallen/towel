# Round-3 audit case misc_block_raises_in_handler_context (misc: exception-context-chaining),
# whose behaviour the audit found kept.
def f1(xs):
    try:
        1 / 0
    except ZeroDivisionError:
        n = len(xs)
        print("f1", n)
        if n:
            raise ValueError("from f1")
    return 0


def f2(ys):
    try:
        1 / 0
    except ZeroDivisionError:
        n = len(ys)
        print("f2", n)
        if n:
            raise ValueError("from f2")
    return 1


if __name__ == "__main__":
    import sys

    import copy
    import re

    def _shown(value):
        return re.sub(r"0x[0-9a-fA-F]+", "0xADDR", repr(value))

    def _calls(label, function, argument_sets):
        for arguments in argument_sets:
            arguments = copy.deepcopy(arguments)
            try:
                outcome = "-> " + _shown(function(*arguments))
            except Exception as error:
                outcome = f"raised {type(error).__name__}: {_shown(str(error))}"
            print(label, outcome, "| arguments after:", _shown(arguments))

    def _value(label, produce):
        try:
            print(label, "=", _shown(produce()))
        except Exception as error:
            print(label, "raised", type(error).__name__, _shown(str(error)))

    pkg_m = sys.modules[__name__]
    def _probe_1():
        r = []
        for f in (pkg_m.f1, pkg_m.f2):
            try:
                r.append(f([1]))
            except ValueError as e:
                r.append((str(e), type(e.__context__).__name__, e.__suppress_context__, e.__cause__))
        __result__ = r
        return __result__
    _value('probe 1', _probe_1)
