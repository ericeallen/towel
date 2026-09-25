# Round-3 audit case late_toplevel_module_in_package (late: cross-top-level-module-inside-
# package). P1-6, --cross-module: pkg/c.py imports helpers_top, a module inside the package
# imported top-level; c.py borrows a helper by a relative import that fails where c.py works.
# Since the P1-6 fix c.py is given no import, and towel dry --cross-module refuses the run.
if __name__ == "__main__":
    import pkg.a as pkg_a
    import pkg
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

    def _probe_1():
        import subprocess, sys
        r = subprocess.run([sys.executable, '-c', 'import c; print(c.fc([1, 2]))'], cwd='pkg', capture_output=True, text=True)
        __result__ = (r.returncode, r.stdout, r.stderr.strip().splitlines()[-1:])
        return __result__
    _value('probe 1', _probe_1)
    _calls('pkg_a.fa', pkg_a.fa, [([1, 2],)])
