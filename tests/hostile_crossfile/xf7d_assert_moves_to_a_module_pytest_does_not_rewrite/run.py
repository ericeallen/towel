# pytest rewrites the asserts of test_values.py and not those of checks.py, so
# a failing assert says more in the one than in the other. A helper shared by
# the two, hosted in checks.py, would take the test's assert out of the module
# pytest rewrites; hosted in test_values.py, it would give checks.py's assert
# pytest's message. Either changes an AssertionError's message.
import contextlib
import io
import sys

import pytest


class Messages:
    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.failed:
            sys.__stdout__.write(f"{report.nodeid}: {report.longrepr.reprcrash.message!r}\n")
            sys.__stdout__.flush()


with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    pytest.main(["-p", "no:cacheprovider", "-q", "pkg"], plugins=[Messages()])

from pkg.checks import check_total  # noqa: E402

try:
    check_total([1, 5])
except AssertionError as error:
    print("plain:", repr(str(error)))
