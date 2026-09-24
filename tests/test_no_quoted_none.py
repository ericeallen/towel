"""No annotation is ever the string ``"None"``, which mypy 2 refuses under ``native_parser``.

The generic candidates quote every annotation they write, ``-> 'None'`` among
them, and packaging and nox enable ``native_parser``: mypy then reported
"Invalid type comment or annotation" and a signature that was right was
refused, the ladder falling back to ``Any`` everywhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy

NATIVE = "[tool.mypy]\nstrict = true\nnative_parser = true\n"


@requires_mypy
def test_a_generic_helper_returning_none_is_accepted_under_the_native_parser(
    tmp_path: Path,
) -> None:
    outcome = apply_one(
        tmp_path,
        """
        def add_number(items: list[int], item: int) -> None:
            items.append(item)
            items.reverse()


        def add_name(names: list[str], name: str) -> None:
            names.append(name)
            names.reverse()
        """,
        pick="add_number and add_name",
        config=NATIVE,
    )
    assert outcome.error is None, outcome.error
    helper = outcome.helper()
    assert isinstance(helper.returns, ast.Constant) and helper.returns.value is None
    assert "_TowelT0" in outcome.signature(), outcome.signature()
    assert "Any" not in outcome.signature(), outcome.signature()
