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

# Runs the checks under pytest, as inline-snapshot runs, and prints each
# outcome. Where the stand-in inline_snapshot.py is present, an installed
# inline-snapshot's plugin is blocked, since the stand-in shadows its package.
import contextlib
import io
from pathlib import Path
import sys

import pytest


class Outcomes:
    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            crash = report.longrepr.reprcrash.message if report.failed else ""
            sys.__stdout__.write(f"{report.nodeid}: {report.outcome} {crash}\n")
            sys.__stdout__.flush()


blocked = ["-p", "no:inline_snapshot"] if (Path(__file__).parent / "inline_snapshot.py").exists() else []
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    pytest.main(["-p", "no:cacheprovider", "-p", "no:randomly", "-q", *blocked, "pkg"], plugins=[Outcomes()])
