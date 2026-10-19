"""Rename semantic NFKC identifiers without confusing them with source spans."""

from pathlib import Path
import subprocess
import sys

import pytest

from towel.changes import apply_changes
from towel.renaming import plan_renames


@pytest.mark.parametrize("newline,bom", [("\n", b""), ("\r\n", b"\xef\xbb\xbf")])
@pytest.mark.parametrize(
    "definition,call,spec,new",
    [
        (
            "def __extracted_func_𝟘(value):\n    return value + 1\n",
            "__extracted_func_0(3)",
            "__extracted_func_0",
            "increment",
        ),
        (
            "def __extracted_func_0(__param_𝟘: int):\n    return __param_0 + 1\n",
            "__extracted_func_𝟘(__param_𝟘=3)",
            "__extracted_func_0.__param_0",
            "value",
        ),
        (
            "class Box:\n    def __extracted_func_𝟘(self, value):\n        return value + 1\n",
            "Box()._Box__extracted_func_𝟘(3)",
            "Box.__extracted_func_0",
            "__increment",
        ),
    ],
)
def test_normalized_identifiers_use_original_token_spans(
    tmp_path: Path, newline: str, bom: bytes, definition: str, call: str, spec: str, new: str
) -> None:
    """Definitions, arguments, keywords and attributes preserve actual execution."""
    # A class-private helper may not be referenced by its explicit stored name.
    if spec.startswith("Box."):
        definition += "    def run(café):\n        return café.__extracted_func_𝟘(3)\n"
        call = "Box().run()"
    prefix = "# café stays byte-for-byte\nuntouched = '𝟘'\n"
    source = prefix + definition + f"print({call})\n"
    path = tmp_path / "main.py"
    original = bom + source.replace("\n", newline).encode()
    path.write_bytes(original)
    before = subprocess.run([sys.executable, str(path)], capture_output=True, check=True).stdout
    plan, count = plan_renames(tmp_path, [(spec, new, path)])
    assert count > 0
    apply_changes(plan)
    result = subprocess.run([sys.executable, str(path)], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == before == b"4\n"
    assert path.read_bytes().startswith(bom + prefix.replace("\n", newline).encode())
    assert path.read_bytes().count(b"\r\n") == original.count(b"\r\n")


def test_normalized_imports_and_global_declarations(tmp_path: Path) -> None:
    """Resolved imports and declarations use the same identity as definitions."""
    helper = tmp_path / "a.py"
    helper.write_text("def __extracted_func_𝟘(value):\n    return value + 1\n")
    consumer = tmp_path / "main.py"
    consumer.write_text(
        "from a import __extracted_func_𝟘\n"
        "def run():\n"
        "    global __extracted_func_𝟘\n"
        "    return __extracted_func_0(3)\n"
        "print(run())\n"
    )
    plan, _ = plan_renames(tmp_path, [("__extracted_func_0", "increment", helper)])
    apply_changes(plan)
    result = subprocess.run([sys.executable, str(consumer)], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"4\n"
    assert "global increment" in consumer.read_text()


@pytest.mark.parametrize("destination", ["K", "K"])
def test_unicode_collision_refuses_atomic_batch(tmp_path: Path, destination: str) -> None:
    """Destination normalization and normalized existing bindings still refuse."""
    path = tmp_path / "main.py"
    original = b"K = 9\ndef __extracted_func_0(value):\n    return value + 1\n"
    path.write_bytes(original)
    other = tmp_path / "other.py"
    other.write_text("def __extracted_func_1():\n    return 2\n")
    before = other.read_bytes()
    with pytest.raises(ValueError):
        plan_renames(
            tmp_path,
            [("__extracted_func_1", "two", other), ("__extracted_func_0", destination, path)],
        )
    assert path.read_bytes() == original
    assert other.read_bytes() == before
