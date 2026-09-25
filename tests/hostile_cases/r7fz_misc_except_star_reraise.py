# Round-3 audit case misc_except_star_reraise (misc: except-star-bare-raise), whose behaviour the
# audit found kept.
def f1(n):
    try:
        raise ExceptionGroup("g", [ValueError(n), TypeError(n)])
    except* ValueError as eg:
        count = len(eg.exceptions)
        print("f1 caught", count)
        if n > 1:
            raise
    except* TypeError:
        print("f1 type")
    return n


def f2(n):
    try:
        raise ExceptionGroup("g", [ValueError(n), TypeError(n)])
    except* ValueError as eg:
        count = len(eg.exceptions)
        print("f2 caught", count)
        if n > 1:
            raise
    except* TypeError:
        print("f2 type")
    return n + 1


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
        try:
            __result__ = pkg_m.f1(1), pkg_m.f1(2)
        except BaseException as e:
            __result__ = (type(e).__name__, str(e), [type(x).__name__ for x in getattr(e, 'exceptions', [])])
        return __result__
    _value('probe 1', _probe_1)
    def _probe_2():
        try:
            __result__ = pkg_m.f2(1), pkg_m.f2(2)
        except BaseException as e:
            __result__ = (type(e).__name__, str(e), [type(x).__name__ for x in getattr(e, 'exceptions', [])])
        return __result__
    _value('probe 2', _probe_2)
