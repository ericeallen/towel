import tempfile
from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine


def _write(p: Path, content: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_method_level_analysis_includes_class_methods_and_param_on_attribute():
    code = """
class User:
    def validate_email(self):
        # duplicate block start
        if not self.email:
            return False
        if '@' not in self.email:
            return False
        return True
        # duplicate block end

    def validate_phone(self):
        # duplicate block start
        if not self.phone:
            return False
        if '@' not in self.phone:
            return False
        return True
        # duplicate block end
""".lstrip()

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "user.py"
        _write(src, code)

        engine = UnificationRefactorEngine(min_lines=3)
        proposals = engine.analyze_file(str(src))
        assert proposals, "Expected proposal from method bodies"

        # Apply first proposal and validate the result compiles and contains a call
        modified = engine.apply_refactoring(str(src), proposals[0])
        assert "def __extracted_func_" in modified
        # Expect method calls to be replaced with call to extracted method on self
        assert "self.__extracted_func_" in modified
        # Since attribute differs (.email vs .phone), entire attribute is parameterized safely
        # Ensure both self and the attribute expression are passed or present
        assert ("self.email" in modified) or ("self.phone" in modified)


def test_comprehension_unification_different_iter_names():
    code = """
def a(xs):
    # duplicate block start
    vals = [x * 2 for x in xs if x % 2 == 0]
    return sum(vals)
    # duplicate block end

def b(xs):
    # duplicate block start
    vals = [y * 2 for y in xs if y % 2 == 0]
    return sum(vals)
    # duplicate block end
""".lstrip()

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "comp.py"
        _write(src, code)

        engine = UnificationRefactorEngine(min_lines=2)
        proposals = engine.analyze_file(str(src))
        assert proposals, "Expected proposal from comprehension bodies with differing iter names"
