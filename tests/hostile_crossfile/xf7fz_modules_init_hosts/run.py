# Round-3 audit case mod_init_hosts (modules: init-as-participant), whose behaviour the audit
# found kept.
if __name__ == "__main__":
    import pkg as pkg
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

    _calls('pkg_a.fa', pkg_a.fa, [([1, 2],)])
    _calls('pkg.fi', pkg.fi, [([1, 2],)])
    def _probe_3():
        import subprocess, sys
        r = subprocess.run([sys.executable, '-c', 'import pkg, sys; print(sorted(k for k in sys.modules if k.startswith(("pkg", "other", "tests"))))'], capture_output=True, text=True)
        __result__ = (r.returncode, r.stdout, r.stderr.strip().splitlines()[-1:] )
        return __result__
    _value('probe 3', _probe_3)
    def _probe_4():
        import subprocess, sys
        r = subprocess.run([sys.executable, '-c', 'import pkg.a, sys; print(sorted(k for k in sys.modules if k.startswith(("pkg", "other", "tests"))))'], capture_output=True, text=True)
        __result__ = (r.returncode, r.stdout, r.stderr.strip().splitlines()[-1:] )
        return __result__
    _value('probe 4', _probe_4)
