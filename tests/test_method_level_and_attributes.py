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

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.test_helpers import write_file
from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine


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
        write_file(src, code)

        engine = UnificationRefactorEngine(min_lines=3)
        proposals = engine.analyze_file(str(src))
        assert proposals, "Expected proposal from method bodies"

        modified = engine.apply_refactoring(str(src), proposals[0])
        tree = ast.parse(modified)
        helper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
        )
        # The receiver is captured only in each site's deferred attribute read.
        # Giving this helper an unread method receiver would restore PARAM-1.
        parameters = [argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)]
        assert len(parameters) == 1 and "self" not in parameters
        assert proposals[0].insert_into_class is None
        assert (
            sum(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == parameters[0]
                for node in ast.walk(helper)
            )
            == 2
        )
        klass = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        for method_name, attribute in (("validate_email", "email"), ("validate_phone", "phone")):
            method = next(
                node
                for node in klass.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            returned = method.body[0]
            assert isinstance(returned, ast.Return) and isinstance(returned.value, ast.Call)
            call = returned.value
            assert isinstance(call.func, ast.Name) and call.func.id == helper.name
            assert len(call.args) == 1 and not call.keywords
            supplier = call.args[0]
            assert isinstance(supplier, ast.Lambda)
            assert not (supplier.args.posonlyargs or supplier.args.args or supplier.args.kwonlyargs)
            assert supplier.args.vararg is None and supplier.args.kwarg is None
            assert isinstance(supplier.body, ast.Attribute) and supplier.body.attr == attribute
            assert isinstance(supplier.body.value, ast.Name) and supplier.body.value.id == "self"

        driver = """
events=[]
class Value:
    def __init__(self, truth, contains):
        self.truth, self.contains = truth, contains
    def __bool__(self):
        events.append('bool')
        return self.truth
    def __contains__(self, marker):
        events.append('contains:'+marker)
        return self.contains
class Observed(User):
    def __init__(self, values, fail=False):
        self.values, self.fail, self.reads = iter(values), fail, 0
    def read(self, attribute):
        events.append('read:'+attribute)
        self.reads += 1
        if self.fail and self.reads == 2:
            raise ValueError('lookup:'+attribute)
        return next(self.values)
    @property
    def email(self): return self.read('email')
    @property
    def phone(self): return self.read('phone')
rows=[]
for attribute in ('email','phone'):
    for truth, contains, fail in ((False,True,False),(True,True,False),(True,False,False),(True,True,True)):
        events.clear()
        user=Observed([Value(truth,not contains),Value(True,contains)],fail)
        try:
            outcome=str(getattr(user,'validate_'+attribute)())
        except ValueError as error:
            outcome='ValueError:'+str(error)
        rows.append((attribute,outcome,list(events)))
print(rows)
"""
        expected = [
            (attribute, outcome, trace)
            for attribute in ("email", "phone")
            for outcome, trace in (
                ("False", ["read:" + attribute, "bool"]),
                ("True", ["read:" + attribute, "bool", "read:" + attribute, "contains:@"]),
                ("False", ["read:" + attribute, "bool", "read:" + attribute, "contains:@"]),
                (
                    "ValueError:lookup:" + attribute,
                    ["read:" + attribute, "bool", "read:" + attribute],
                ),
            )
        ]
        for source in (code, modified):
            result = subprocess.run(
                [sys.executable, "-B", "-c", source + "\n" + driver],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert result.stderr == ""
            assert ast.literal_eval(result.stdout) == expected
        assert src.read_text() == code


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
        write_file(src, code)

        engine = UnificationRefactorEngine(min_lines=2)
        proposals = engine.analyze_file(str(src))
        assert proposals, "Expected proposal from comprehension bodies with differing iter names"
