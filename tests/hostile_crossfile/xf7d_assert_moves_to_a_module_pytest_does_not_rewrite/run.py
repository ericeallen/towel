# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pytest rewrites the asserts of check_values.py, which python_files names,
# and not those of checks.py, so a failing assert says more in the one than
# in the other. A helper shared by the two, hosted in checks.py, would take
# the test's assert out of the module pytest rewrites; hosted in
# check_values.py, it would give checks.py's assert pytest's message. Either
# changes an AssertionError's message.
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
