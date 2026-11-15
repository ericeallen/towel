# Copyright 2026 Eric Allen
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

"""Runtime item 11: evidence for the single relaxed suitability notice."""

from pathlib import Path

from towel.relaxed_suitability import relaxed_findings, relaxed_notice


def test_lifetime_observers_have_paths_and_lines() -> None:
    """Runtime 11: recognise finalizers and aliased callbacks, not plain weakrefs."""
    source = """import weakref as wr
from weakref import ref as reference
import gc as collector
class Resource:
    def __del__(self): pass
wr.finalize(obj, cleanup)
reference(obj, callback=cleanup)
wr.proxy(obj, cleanup)
collector.callbacks.append(cleanup)
wr.ref(obj)
wr.ref(obj, None)
"""
    findings = relaxed_findings({Path("/project/model.py"): source}, root=Path("/project"))
    assert [(item.kind, item.path, item.line) for item in findings] == [
        ("finalizer", "model.py", 5),
        ("weakref_callback", "model.py", 6),
        ("weakref_callback", "model.py", 7),
        ("weakref_callback", "model.py", 8),
        ("gc_callback", "model.py", 9),
    ]
    assert findings[0].to_json()["line"] == 5
    notice = relaxed_notice(findings)
    assert "__del__" in notice and "model.py:5" in notice
    assert "--strict" in notice and "strict = true" in notice


def test_rebinding_analysed_module_functions() -> None:
    """Runtime 11: report known functions replaced by writes and mock patch calls."""
    sources = {
        Path("/project/pkg/model.py"): "def compute(): pass\ncompute = replacement\n",
        Path("/project/test_model.py"): """import pkg.model as model
from unittest.mock import patch as replace
model.compute = replacement
replace('pkg.model.compute', replacement)
replace.object(model, 'compute', replacement)
model.unrelated = replacement
replace('external.compute', replacement)
""",
    }
    findings = relaxed_findings(sources, root=Path("/project"))
    assert [(item.path, item.line) for item in findings] == [
        ("pkg/model.py", 2),
        ("test_model.py", 3),
        ("test_model.py", 4),
        ("test_model.py", 5),
    ]
    assert all(item.kind == "function_rebinding" for item in findings)


def test_no_findings_no_notice_and_no_false_plain_local_write() -> None:
    """Runtime 11: ordinary locals and similarly named APIs do not imply risk."""
    source = """import weakref
class Ordinary:
    def finalize(self): pass
def compute(): pass
def another():
    compute = 3
    return compute
weakref.ref(obj, callback=None)
other.finalize(obj, cleanup)
other.callbacks.append(cleanup)
"""
    findings = relaxed_findings({Path("/project/model.py"): source}, root=Path("/project"))
    assert findings == ()
    assert relaxed_notice(findings) == ""


def test_global_rebinding_and_source_root_layout() -> None:
    """Runtime 11: src layouts and explicit global writes remain discoverable."""
    sources = {
        Path("/project/src/pkg/model.py"): """def compute(): pass
def replace():
    global compute
    compute = replacement
""",
        Path("/project/test.py"): """from unittest import mock
mock.patch('pkg.model.compute', replacement)
""",
    }
    findings = relaxed_findings(sources, root=Path("/project"))
    assert [(item.path, item.line) for item in findings] == [
        ("src/pkg/model.py", 4),
        ("test.py", 2),
    ]


def test_invalid_sources_contribute_no_notice() -> None:
    """Runtime 11: invalid skipped input is not represented as analysed evidence."""
    assert relaxed_findings({Path("/project/bad.py"): "broken ("}, root=Path("/project")) == ()


def test_shadowed_imports_are_not_reported() -> None:
    """Runtime 11: a parameter sharing an API's name is not evidence of that API."""
    source = """import weakref
import gc
def ordinary(weakref, gc):
    weakref.finalize(obj, cleanup)
    gc.callbacks.append(cleanup)
"""
    assert relaxed_findings({Path("/project/model.py"): source}, root=Path("/project")) == ()


def test_callback_collection_assignment_is_reported() -> None:
    """Runtime 11: installing callbacks by assignment is a lifetime observer too."""
    source = """from gc import callbacks
import gc
callbacks[:] = [cleanup]
gc.callbacks += [cleanup]
"""
    findings = relaxed_findings({Path("/project/model.py"): source}, root=Path("/project"))
    assert [(item.kind, item.line) for item in findings] == [("gc_callback", 3), ("gc_callback", 4)]


def test_relative_package_import_and_setattr_rebinding() -> None:
    """Runtime 11: package imports and explicit setattr writes identify known functions."""
    sources = {
        Path("/project/pkg/__init__.py"): "from . import model\nmodel.compute = replacement\n",
        Path("/project/pkg/model.py"): "def compute(): pass\n",
        Path(
            "/project/test.py"
        ): 'import pkg.model as model\nsetattr(model, "compute", replacement)\n',
    }
    findings = relaxed_findings(sources, root=Path("/project"))
    assert [(item.path, item.line) for item in findings] == [("pkg/__init__.py", 2), ("test.py", 2)]


def test_replaced_import_does_not_claim_standard_library_api() -> None:
    """Runtime 11: an explicitly replaced import is no longer evidence for an API."""
    source = "import weakref\nweakref = custom\nweakref.finalize(obj, cleanup)\n"
    assert relaxed_findings({Path("/project/model.py"): source}, root=Path("/project")) == ()
