"""A refactoring never adds, removes or moves the evaluation of an annotation.

An annotation is an expression, and unless the module defers its annotations
the interpreter evaluates it once, where the ``def`` is executed. Extracting a
helper copies the call site's annotation onto the helper's parameter, so the
copy is a second occurrence of that expression, evaluated a second time at
import. ``Annotated[int, mark('a')]`` is a valid annotation whose metadata a
call builds; pydantic's ``Field(...)`` and FastAPI's ``Depends(...)`` are the
same shape in wide use. The extra call is a change in what the program does,
and every type checker reads an annotation for its type rather than running
it, so no amount of checking can see it: the original and the generated source
both pass strict mypy while the generated one calls ``mark`` one more time.

The oracle here is therefore the program's own behaviour. Each test imports
the module before the refactoring and again after it and compares a
module-level list that records every evaluation, rather than asking whether
the result type-checks or how the annotation is spelled. Quoting the copied
annotation is what keeps the two equal: a string annotation is the same type
to a checker and inert to the interpreter.

``from __future__ import annotations`` defers every annotation in a module,
which hides this entire class of defect -- it was hidden once already by a
fixture that carried the import. Only the one test that exists to show the
deferred case uses it here.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
from pathlib import Path
import textwrap
from typing import List, Optional, Sequence, Tuple

import pytest

from tests.test_cli_integration import invoke
from towel.unification.annotations import respell_bare

_load_counter = itertools.count()

STRICT_MYPY = "[tool.mypy]\nstrict = true\n"

BLOCK = """
    doubled = value + 1
    scaled = doubled * 2
    scaled = scaled + 5
"""

METADATA_PROGRAM = f"""
from typing import Annotated

evaluated: list[str] = []


def mark(name: str) -> str:
    evaluated.append(name)
    return name


def first(value: Annotated[int, mark('first')]) -> int:{BLOCK}    return scaled + 1


def second(value: Annotated[int, mark('second')]) -> int:{BLOCK}    return scaled + 2
"""

QUOTED_PROGRAM = METADATA_PROGRAM.replace(
    "value: Annotated[int, mark('first')]",
    "value: \"Annotated[int, mark('first')]\"",
).replace(
    "value: Annotated[int, mark('second')]",
    "value: \"Annotated[int, mark('second')]\"",
)

DEFERRED_PROGRAM = "from __future__ import annotations\n" + METADATA_PROGRAM


def _evaluations(module_path: Path) -> List[str]:
    """What ``module_path`` records having evaluated while it is imported.

    Loaded under a fresh name every time, so importing the same path before
    and after the rewrite runs it twice rather than returning one cached
    module the second time.
    """
    name = f"_towel_annotation_effects_{next(_load_counter)}"
    specification = importlib.util.spec_from_file_location(name, module_path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    evaluated = module.evaluated
    assert isinstance(evaluated, list)
    return list(evaluated)


def _helper(source: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name:
            return node
    raise AssertionError(f"no helper in:\n{source}")


def _refactored_in_place(
    tmp_path: Path, program: str, *arguments: str
) -> Tuple[List[str], List[str], str]:
    """Run the real CLI over ``program`` and return its log before, after, and the source.

    In place and capped at one refactoring, the way the audit reproduced this,
    so the comparison is between two executions of one file.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(STRICT_MYPY)
    module = project / "m.py"
    module.write_text(textwrap.dedent(program).lstrip())
    before = _evaluations(module)
    result = invoke(
        [
            "dry",
            str(project),
            str(project),
            "--no-interactive",
            "--progress",
            "none",
            "--no-format",
            "--max-refactorings",
            "1",
            *arguments,
        ]
    )
    assert result.status == 0, result.stdout + result.stderr
    source = module.read_text()
    _helper(source)  # a run that extracted nothing would witness nothing
    return before, _evaluations(module), source


def test_copying_an_annotation_does_not_evaluate_its_metadata_again(tmp_path: Path) -> None:
    """The defect itself: the helper's copy of ``Annotated[int, mark('first')]``.

    Written bare onto the helper, the copy calls ``mark`` where nothing called
    it before, so a module that recorded ``['first', 'second']`` records
    ``['first', 'first', 'second']``. The helper must still carry the type --
    dropping the annotation would equalize the logs too, and would be a
    different regression -- so the signature is checked for it as well.
    """
    before, after, source = _refactored_in_place(tmp_path, METADATA_PROGRAM)
    assert before == ["first", "second"]
    assert after == before
    annotation = _helper(source).args.args[0].annotation
    assert annotation is not None
    assert "mark(" in ast.unparse(annotation)


def test_a_quoted_annotation_is_not_unquoted_into_an_evaluated_one(tmp_path: Path) -> None:
    """Quoting in the source is a decision of the program's, not a spelling.

    Both call sites write their annotation as a string, so ``mark`` is never
    called and the module's log is empty. Respelling the helper's annotation
    bare -- every name it uses is in scope by the time the helper is defined --
    would call ``mark`` for the first time in the program's life. The
    quotation has to survive the refactoring.
    """
    before, after, _source = _refactored_in_place(tmp_path, QUOTED_PROGRAM)
    assert before == []
    assert after == []


def test_an_unannotated_helper_evaluates_nothing_to_begin_with(tmp_path: Path) -> None:
    """``--no-types`` writes no annotation, so there is nothing extra to evaluate.

    The control for the cases above: it shows the log is unmoved by the
    extraction itself, so what moved it in the default run is the copied
    annotation rather than the new function.
    """
    before, after, source = _refactored_in_place(tmp_path, METADATA_PROGRAM, "--no-types")
    assert before == ["first", "second"]
    assert after == before
    assert all(argument.annotation is None for argument in _helper(source).args.args)


def test_a_module_that_defers_annotations_evaluates_none_of_them(tmp_path: Path) -> None:
    """The one fixture allowed the ``__future__`` import, and why it is quarantined.

    With annotations deferred nothing in any annotation is ever evaluated, so
    the log is empty before and after however the helper is spelled. A fixture
    carrying this import proves nothing about the defect above, and it is kept
    here only so the deferred case is covered rather than assumed.
    """
    before, after, _source = _refactored_in_place(tmp_path, DEFERRED_PROGRAM)
    assert before == []
    assert after == []


def _respelled(annotation: str, module: str, bare_ok: Sequence[str] = ()) -> Optional[str]:
    """How ``respell_bare`` writes a quoted helper annotation; None when it stays quoted."""
    helper = ast.parse(f"def helper(value: {annotation}) -> None: ...").body[0]
    assert isinstance(helper, ast.FunctionDef)
    respelled = respell_bare(helper, ast.parse(textwrap.dedent(module)), set(bare_ok))
    written = respelled.args.args[0].annotation
    assert written is not None
    if isinstance(written, ast.Constant) and isinstance(written.value, str):
        return None
    return ast.unparse(written)


IMPORTS = "from typing import Annotated, Optional\n"


def test_respell_bare_unquotes_only_annotations_that_run_nothing() -> None:
    """The mechanism, at the one function that turns a string back into an expression.

    Resolvability is not the test it needs. ``mark`` resolves where the helper
    is defined, and that is exactly what makes unquoting ``Annotated[int,
    mark('a')]`` dangerous rather than safe; a conditional or a comprehension
    in an annotation is the same. An annotation built only from names,
    subscripts and unions runs none of the program's code and is still
    unquoted, so the rule stays a rule about evaluation rather than a blanket
    refusal that would leave every helper quoted.
    """
    assert _respelled("'Optional[int]'", IMPORTS) == "Optional[int]"
    assert _respelled("'list[int] | None'", IMPORTS) == "list[int] | None"
    assert _respelled("'Annotated[int, MARKER]'", IMPORTS, ["MARKER"]) == "Annotated[int, MARKER]"
    assert _respelled("\"Annotated[int, mark('a')]\"", IMPORTS, ["mark"]) is None
    assert _respelled("'list[int] if FLAG else str'", IMPORTS, ["FLAG"]) is None
    assert _respelled("'tuple[int, *Ts]'", IMPORTS, ["Ts"]) == "tuple[int, *Ts]"


@pytest.mark.parametrize("program", [METADATA_PROGRAM, QUOTED_PROGRAM, DEFERRED_PROGRAM])
def test_every_fixture_here_runs_before_it_is_refactored(program: str) -> None:
    """A fixture that does not run cannot witness a change in how it runs."""
    compile(textwrap.dedent(program).lstrip(), "<fixture>", "exec")
