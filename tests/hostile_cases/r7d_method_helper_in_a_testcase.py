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

# Two tests of one TestCase share a block that reads the case's attributes, so
# the helper becomes a class-private method of the case, _Case__extracted_func_0.
# Neither unittest nor pytest may collect it as a test: both runners list and
# run the same tests, with the same outcomes, before and after.
import contextlib
import io
import os
import sys
import unittest


class Case(unittest.TestCase):
    scale = 3

    def test_small(self):
        items = [1, 2]
        total = 0
        for item in items:
            total = total + item * self.scale
        result = total + 1
        self.assertEqual(result, 10)

    def test_large(self):
        items = [4, 5]
        total = 0
        for item in items:
            total = total + item * self.scale
        result = total + 1
        self.assertEqual(result, 99)


class Recorder:
    def pytest_collection_finish(self, session):
        names = sorted(item.nodeid.split("::", 1)[1] for item in session.items)
        print("pytest collected", names, file=sys.__stdout__)

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            print("pytest", report.nodeid.split("::", 1)[1], report.outcome, file=sys.__stdout__)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    print("unittest collects", loader.getTestCaseNames(Case))
    result = unittest.TestResult()
    loader.loadTestsFromTestCase(Case).run(result)
    print("unittest ran", result.testsRun, "failures", [str(test) for test, _ in result.failures])
    import pytest

    recorder = Recorder()
    with contextlib.redirect_stdout(io.StringIO()):
        pytest.main(
            ["-q", "-p", "no:cacheprovider", "--noconftest", "-c", os.devnull, __file__],
            plugins=[recorder],
        )
    sys.stdout.flush()
