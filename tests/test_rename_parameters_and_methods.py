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

"""Renaming helper parameters and class-level helpers for LLM-driven naming."""

from __future__ import annotations

from pathlib import Path

import pytest

from towel.changes import apply_changes
from towel.renaming import plan_renames


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _apply(target: Path, mapping: dict[str, str]) -> int:
    plan, count = plan_renames(target, [(old, new, None) for old, new in mapping.items()])
    apply_changes(plan)
    return count


def test_parameter_rename_covers_signature_thunk_calls_and_closures(tmp_path: Path) -> None:
    target = _project(
        tmp_path,
        {
            "m.py": (
                "def __extracted_func_0(__param_0, items):\n"
                "    if not __param_0():\n"
                "        raise ValueError\n"
                "    return [__param_0() for _ in items] + [lambda: __param_0()]\n"
                "def caller(self, items):\n"
                "    return __extracted_func_0(lambda: self.email, items)\n"
            )
        },
    )
    count = _apply(
        target, {"__extracted_func_0.__param_0": "email", "__extracted_func_0": "require_email"}
    )
    text = (target / "m.py").read_text()
    assert "def require_email(email, items):" in text
    assert "if not email():" in text and "[email() for _ in items] + [lambda: email()]" in text
    assert "return require_email(lambda: self.email, items)" in text
    assert "__param_0" not in text
    assert count >= 5


def test_parameter_rename_rejects_collision_with_body_name(tmp_path: Path) -> None:
    target = _project(
        tmp_path,
        {
            "m.py": "def __extracted_func_0(__param_0, items):\n    total = len(items)\n    return __param_0 + total\n"
        },
    )
    with pytest.raises(ValueError, match="collide"):
        plan_renames(target, [("__extracted_func_0.__param_0", "total", None)])
    with pytest.raises(ValueError, match="collide"):
        plan_renames(target, [("__extracted_func_0.__param_0", "len", None)])


def test_parameter_rename_unknown_parameter_is_reported(tmp_path: Path) -> None:
    target = _project(tmp_path, {"m.py": "def __extracted_func_0(a):\n    return a\n"})
    with pytest.raises(ValueError, match="No helper defines"):
        plan_renames(target, [("__extracted_func_0.__param_9", "x", None)])


def test_parameter_rename_leaves_shadowing_inner_scope_alone(tmp_path: Path) -> None:
    target = _project(
        tmp_path,
        {
            "m.py": (
                "def __extracted_func_0(__param_0):\n"
                "    def inner(__param_0):\n"
                "        return __param_0\n"
                "    return inner(__param_0)\n"
            )
        },
    )
    _apply(target, {"__extracted_func_0.__param_0": "value"})
    text = (target / "m.py").read_text()
    assert text == (
        "def __extracted_func_0(value):\n"
        "    def inner(__param_0):\n"
        "        return __param_0\n"
        "    return inner(value)\n"
    )


def test_class_helper_rename_updates_every_attribute_reference(tmp_path: Path) -> None:
    target = _project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": (
                "class Base:\n"
                "    def _extracted_func_3(self, x):\n        return x + 1\n"
                "    def run(self):\n        return self._extracted_func_3(1)\n"
                "class Child(Base):\n"
                "    def go(self):\n        return self._extracted_func_3(2)\n"
            ),
            "pkg/b.py": "from .a import Child\ndef use():\n    return Child()._extracted_func_3(3)\n",
        },
    )
    _apply(target, {"_extracted_func_3": "_increment"})
    a = (target / "pkg" / "a.py").read_text()
    b = (target / "pkg" / "b.py").read_text()
    assert "def _increment(self, x):" in a and a.count("self._increment(") == 2
    assert "Child()._increment(3)" in b
    assert "_extracted_func_3" not in a + b


def test_class_helper_rename_refuses_existing_or_dynamic_names(tmp_path: Path) -> None:
    target = _project(
        tmp_path,
        {
            "m.py": (
                "class A:\n"
                "    def _extracted_func_0(self):\n        return 1\n"
                "    def taken(self):\n        return getattr(self, '_extracted_func_0')()\n"
            )
        },
    )
    with pytest.raises(ValueError, match="already appears"):
        plan_renames(target, [("_extracted_func_0", "taken", None)])
    with pytest.raises(ValueError, match="referenced by name"):
        plan_renames(target, [("_extracted_func_0", "fresh", None)])
