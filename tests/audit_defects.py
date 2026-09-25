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

"""The defects a release audit reported that the suite still expects to fail, as one line each.

Each constant here is the reason of a strict expected failure
(:func:`tests.hostile_refactoring.with_known_defects`): the batteries' fixtures
and the differential seeds that expose a defect name it in their
``KNOWN_DEFECTS``, so the fix of one defect flips every test that shows it,
and the reason says which. Write one as ``"<audit id>: <the defect, in a
line>"``, and remove it once nothing names it.

None is open. The round-3 audit's eight P1s (Towel 1.772 at 077a712,
dimension 2, adversarial semantic equivalence) are all fixed, and every
fixture and seed ported for them passes.
"""
