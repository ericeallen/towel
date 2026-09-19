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

"""The engine's defaults, shared by the constructor, the drivers and the command line."""

DEFAULT_MAX_PARAMETERS = 5
"""Most parameters an extracted helper may take."""

DEFAULT_MIN_LINES = 3
"""Fewest lines a candidate block may span."""

DEFAULT_MAX_ITERATIONS = 0
"""Applied refactorings after which a driver stops; zero runs to a fixed point."""

DEFAULT_MAX_CANDIDATE_PAIRS = 20_000_000
"""Candidate block pairs an analysis evaluates before it starts leaving buckets out.

Above every project in the ecosystem corpus (networkx needs 9.25 million,
sphinx 8.45 million), so real projects are analyzed in full; a pathological
file of hundreds of near-identical long functions still stops here. The
pairs themselves take about 150 bytes each, about 3 GB at the limit.
"""
