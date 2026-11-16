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

import pytest

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
        Path(
            "/project/pkg/model.py"
        ): "def compute(): pass\ndef install():\n    global compute\n    compute = replacement\n",
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
        ("pkg/model.py", 4),
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


@pytest.mark.parametrize(
    "replacement",
    [
        "from unittest.mock import patch\npatch('pkg.model.compute', replacement)\n",
        "from pkg import model\nmodel.compute = replacement\n",
        "from . import model\nmodel.compute = replacement\n",
    ],
)
def test_package_target_keeps_qualified_module_names(replacement: str) -> None:
    """Review D3: targeting a package preserves absolute and relative patch targets."""
    root = Path("/project/pkg")
    sources = {
        root / "__init__.py": "",
        root / "model.py": "def compute(): pass\n",
        root / "consumer.py": replacement,
    }
    findings = relaxed_findings(sources, root=root)
    assert [(item.kind, item.path, item.line) for item in findings] == [
        ("function_rebinding", "consumer.py", 2)
    ]
    assert "pkg.model.compute" in findings[0].detail


@pytest.mark.parametrize(
    "replacement",
    [
        "monkeypatch.setattr(model, 'compute', replacement)",
        "monkeypatch.setattr('model.compute', replacement)",
    ],
)
def test_pytest_monkeypatch_replacements(replacement: str) -> None:
    """Review D3: pytest's object and dotted-string patch forms are observable writes."""
    sources = {
        Path("/project/model.py"): "def compute(): pass\n",
        Path("/project/test_model.py"): "import model\ndef test_compute(monkeypatch):\n    "
        + replacement
        + "\n",
    }
    findings = relaxed_findings(sources, root=Path("/project"))
    assert [(item.kind, item.line, item.detail) for item in findings] == [
        ("function_rebinding", 3, "patch of model.compute")
    ]


@pytest.mark.parametrize(
    "source, line",
    [
        ("def close(self): pass\nclass R:\n    __del__ = close\n", 3),
        ("class R:\n    __del__ = lambda self: None\n", 2),
        ("class R:\n    __del__: object = lambda self: None\n", 2),
        ("class R: pass\nR.__del__ = lambda self: None\n", 2),
        ('class R: pass\nsetattr(R, "__del__", lambda self: None)\n', 2),
    ],
)
def test_assigned_finalizers(source: str, line: int) -> None:
    """Review D3: assigning a class finalizer observes lifetimes just as a def does."""
    findings = relaxed_findings({Path("/project/model.py"): source}, root=Path("/project"))
    assert [(item.kind, item.line) for item in findings] == [("finalizer", line)]
    assert "R.__del__" in findings[0].detail


@pytest.mark.parametrize(
    "replacement",
    [
        "setattr(sys.modules[__name__], 'compute', replacement)",
        "sys.modules[__name__].compute = replacement",
        "globals()['compute'] = replacement",
    ],
)
def test_reflective_current_module_replacements(replacement: str) -> None:
    """Review D3: explicit reflective writes name their known function precisely."""
    source = "import sys\ndef compute(): pass\ndef install():\n    " + replacement + "\n"
    findings = relaxed_findings({Path("/project/model.py"): source}, root=Path("/project"))
    assert [(item.kind, item.line) for item in findings] == [("function_rebinding", 4)]
    assert "model.compute" in findings[0].detail


@pytest.mark.parametrize(
    "replacement",
    [
        "compute = functools.lru_cache(maxsize=None)(compute)",
        'if sys.platform == "win32":\n    compute = fallback',
        "compute = fallback",
    ],
)
def test_import_time_function_choices_are_quiet(replacement: str) -> None:
    """Review D3: import-time decoration and platform choices are definitions, not patches."""
    source = (
        "import sys, functools\ndef compute(): pass\ndef fallback(): pass\n" + replacement + "\n"
    )
    assert relaxed_findings({Path("/project/model.py"): source}, root=Path("/project")) == ()


@pytest.mark.parametrize(
    "body, expected",
    [
        ("def outer():\n    def inner():\n        global compute\n        compute = other\n", True),
        ("def outer():\n    class Inner:\n        global compute\n        compute = other\n", True),
        ("class Outer:\n    global compute\n    compute = other\n", False),
        ("def outer():\n    global compute\n    def inner():\n        compute = other\n", False),
    ],
)
def test_global_replacement_obeys_lexical_scope(body: str, expected: bool) -> None:
    """Review D3: only global writes made inside a running function are replacements."""
    findings = relaxed_findings(
        {Path("/project/model.py"): "def compute(): pass\n" + body}, root=Path("/project")
    )
    assert bool(findings) == expected
    assert all(f.kind == "function_rebinding" for f in findings)


def test_local_class_finalizer_assignment_is_reported() -> None:
    """Review D3: local class attributes are lifetime observers too."""
    source = "def factory():\n    class R: pass\n    R.__del__ = lambda self: None\n"
    findings = relaxed_findings({Path("/project/model.py"): source}, root=Path("/project"))
    assert [(f.kind, f.line) for f in findings] == [("finalizer", 3)]


@pytest.mark.parametrize(
    "body",
    [
        "def install(globals):\n    globals()['compute'] = other\n",
        "def install(setattr):\n    setattr(sys.modules[__name__], 'compute', other)\n",
        "def install():\n    def globals(): return {}\n    globals()['compute'] = other\n",
        "def test_compute(monkeypatch):\n    monkeypatch = other\n    monkeypatch.setattr('model.compute', other)\n",
    ],
)
def test_shadowed_reflective_apis_do_not_claim_a_patch(body: str) -> None:
    """Review D3: a similarly named local callable or reassigned fixture is not patch evidence."""
    source = "import sys\ndef compute(): pass\n" + body
    assert relaxed_findings({Path("/project/model.py"): source}, root=Path("/project")) == ()
