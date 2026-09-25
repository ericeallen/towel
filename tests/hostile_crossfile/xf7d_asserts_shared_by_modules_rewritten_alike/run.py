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
