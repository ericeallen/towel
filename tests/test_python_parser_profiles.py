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

"""Python support lane: native diagnostics must survive every cache hit."""

import ast
import re
import sys
import warnings

import pytest

from towel.analysis_sources import parse_analysis_source, parsing_is_pure, sharing_analysis_sources
from towel.unification.import_graph import ImportTimeCode


@pytest.mark.skipif(
    sys.version_info < (3, 15), reason="relative lazy import warning is new in 3.15"
)
@pytest.mark.parametrize(
    "source", ["from . lazy import x", "from ... lazy import x", "from . \\\n lazy import x"]
)
def test_relative_lazy_import_repeats_native_warning(source: str) -> None:
    """Python lane: the new parser warning must not disappear on a warm cache."""
    assert not parsing_is_pure(source)
    with warnings.catch_warnings(record=True) as native:
        warnings.simplefilter("always")
        ast.parse(source)
    assert len(native) == 1
    with sharing_analysis_sources(), warnings.catch_warnings(record=True) as actual:
        warnings.simplefilter("always")
        for _ in range(2):
            parse_analysis_source(source, "probe.py")
    assert [(w.category, str(w.message)) for w in actual] == [
        (native[0].category, str(native[0].message))
    ] * 2
    with sharing_analysis_sources(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parse_analysis_source(source, "probe.py")
        warnings.simplefilter("error")
        with pytest.raises(SyntaxError):
            parse_analysis_source(source, "probe.py")


@pytest.mark.parametrize("pattern", ["[[a]", "[a--b]", "[a&&b]", "[a~~b]", "[a||b]"])
def test_regex_warning_sites_remain_effectful_after_native_cache_warms(pattern: str) -> None:
    """Python lane: re.compile's cache cannot certify away a warning."""
    re.purge()
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        try:
            re.compile(pattern)
        except re.error:
            pass  # A bad range can warn before raising; both effects must remain.
    assert any(w.category is FutureWarning for w in emitted)
    source = f"import re\nPATTERN = re.compile({pattern!r})\n"
    assert ImportTimeCode(source).statements() == (False, True)


def test_warning_free_regex_is_quiet_on_reviewed_interpreters() -> None:
    """Python lane: certify the positive case alongside native warning sites."""
    source = "import re\nPATTERN = re.compile(r'[a-z]+', re.IGNORECASE)\n"
    assert ImportTimeCode(source).statements() == (False, False)
