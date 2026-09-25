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

# The program's own run, and coverage.py's account of it as the project
# configures it: which files it measures, and how many of their statements
# ran. Where coverage.py is not installed only the program runs.
import io
import sys

try:
    import coverage
except ImportError:
    coverage = None

measuring = coverage.Coverage(data_file=None) if coverage is not None else None
if measuring is not None:
    measuring.start()
from pkg import a, b  # noqa: E402

print(b.used([1]), a.__name__)
if measuring is not None:
    measuring.stop()
    report = io.StringIO()
    measuring.report(file=report)
    for line in report.getvalue().splitlines():
        if line.startswith("pkg"):
            print(" ".join(line.split()[:3]))
