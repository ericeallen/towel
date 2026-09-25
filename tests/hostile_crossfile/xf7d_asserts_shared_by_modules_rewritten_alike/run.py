# Neither module is one pytest rewrites, so a helper either hosts keeps every
# assert's message: the pair is extracted across the two, and each failing
# assert reads as it did, under pytest and without it.
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

from pkg.alpha import check_total  # noqa: E402
from pkg.beta import check_scaled  # noqa: E402

for check, items in ((check_total, [1, 5]), (check_scaled, [2, 4])):
    try:
        check(items)
    except AssertionError as error:
        print("plain:", repr(str(error)))
