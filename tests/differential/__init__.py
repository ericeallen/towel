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

"""Differential testing of Towel on generated near-duplicate code.

A seeded generator (:mod:`tests.differential.grammar`) writes a small
project whose functions share a block that differs at a few holes. Towel
refactors a copy of it through its library entry, exactly as the hostile
batteries run it (:mod:`tests.differential.runner`), and an observer run in
a fresh interpreter (:mod:`tests.differential.observer`) records everything
the original and the refactored program can be seen to do. The two records
are compared (:mod:`tests.differential.comparison`); any behaviour
difference is a defect, reported with the seed and the generated source.

``tests/test_differential_grammar.py`` runs a fixed, measured set of seeds in
the default suite. :mod:`tests.differential.fuzz` (``just fuzz``) draws many
more, in the default and the cross-module modes, and writes each failure as a
hostile fixture ready to commit (:mod:`tests.differential.export`).

The generator, the observer and the runner descend from the round-3 release
auditor's harness. Seed ``N`` generates, byte for byte, the case the audit
called ``gram_uNNNN`` (``gram_tNNNNN`` when typed) for each of the 1300 cases
it kept from its final generator: untyped seeds 400 to 899 and 1000 to 1299,
typed seeds 10400 to 10599 and 11000 to 11299. Its first batch came from an
earlier revision.
"""
