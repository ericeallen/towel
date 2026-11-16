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

"""Reviewed Pyright 1.1.414 workspace profiles; CLI checking remains required."""

import sys

from towel.type_inference import PyrightOracle


def native_workspace_completion_refused() -> bool:
    """Pyright's implicit 3.14/3.15 target exposes its templatelib progress bug.

    The live configured-target regression in test_pyright_session verifies
    both sides independently of the interpreter running Towel. Tests using
    the native target retain semantic checks and assert the CLI fallback on
    these interpreters. This profile is independent of lifetime certification.
    """
    return sys.version_info[:2] in {(3, 14), (3, 15)}


def assert_native_workspace_profile(oracle: PyrightOracle) -> None:
    """Require the reviewed backend while callers keep all verdict assertions."""
    if native_workspace_completion_refused():
        assert oracle._server is None and not oracle._warmed
    else:
        assert oracle._warmed and oracle.answered_from_a_session
