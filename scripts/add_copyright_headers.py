#!/usr/bin/env python3
"""Add Apache 2.0 copyright headers to all Python source files."""

from pathlib import Path

COPYRIGHT_HEADER = """# Copyright 2025-2026 Eric Allen
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

"""


def has_copyright(content: str) -> bool:
    """Check if file already has a copyright header."""
    return "Copyright" in content[:500] and "Apache License" in content[:500]


def add_header(file_path: Path) -> bool:
    """Add copyright header to a file. Returns True if modified."""
    content = file_path.read_text()

    if has_copyright(content):
        print(f"SKIP {file_path} (already has copyright)")
        return False

    # Handle shebang lines
    lines = content.split("\n")
    if lines and lines[0].startswith("#!"):
        # Keep shebang, add header after it
        new_content = lines[0] + "\n" + COPYRIGHT_HEADER + "\n".join(lines[1:])
    else:
        new_content = COPYRIGHT_HEADER + content

    file_path.write_text(new_content)
    print(f"ADDED {file_path}")
    return True


def main():
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src" / "towel"

    modified_count = 0
    skipped_count = 0

    # Process all Python files in src/towel/
    for py_file in sorted(src_dir.rglob("*.py")):
        if add_header(py_file):
            modified_count += 1
        else:
            skipped_count += 1

    print(f"\nSummary: {modified_count} files modified, {skipped_count} files skipped")


if __name__ == "__main__":
    main()
