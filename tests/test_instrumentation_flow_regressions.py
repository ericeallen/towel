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

"""Instrumentation follows actual argument flow and hook delegation, not mere presence.

These are the independent review's small counterexamples. They test the
recognition boundary without importing typeguard or executing its decorators;
the source-compilation cases spell the complete read, compile, and install
flow that made the hostile runtime fixture lose its assignment tracing.
"""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from tests.test_instrumentation_forms import refusal


@pytest.mark.parametrize(
    "source",
    [
        "class Base:\n"
        "    def __init_subclass__(cls):\n"
        "        from typeguard import typechecked as checked\n"
        "        checked(cls)\n"
        "class C(Base):\n    def f(self): return 1\n",
        "from typeguard import typechecked as checked\n"
        "class Base:\n"
        "    def __init_subclass__(cls):\n"
        "        apply = checked\n"
        "        apply(cls)\n"
        "class C(Base):\n    def f(self): return 1\n",
        "from typeguard import typechecked as checked\n"
        "class Base:\n"
        "    def __init_subclass__(cls):\n"
        "        target = cls\n"
        "        checked(target)\n"
        "class C(Base):\n    def f(self): return 1\n",
        "from typeguard import typechecked as checked\n"
        "class C:\n    def f(self): return 1\n"
        "def setup():\n"
        "    target = C\n"
        "    checked(target)\n"
        "setup()\n",
    ],
    ids=["local-import", "local-callee-alias", "hook-argument-alias", "ordinary-argument-alias"],
)
def test_local_bindings_preserve_the_instrumenter_and_its_target(
    tmp_path: Path, source: str
) -> None:
    """Changing a known callee or class to a local alias cannot lose its protection."""
    assert refusal(tmp_path, source) == "typeguard.typechecked"


def test_a_source_compiler_with_local_imports_still_instruments_methods(tmp_path: Path) -> None:
    """Moving inspect into the compiler must not turn a body transform into an unknown call."""
    source = """
    def recompile(method):
        import inspect
        import textwrap
        source = textwrap.dedent(inspect.getsource(method))
        code = compile(source, inspect.getsourcefile(method), "exec")
        rewritten = {}
        exec(code, method.__globals__, rewritten)
        return rewritten[method.__name__]

    class Meta(type):
        def __new__(meta, name, bases, namespace):
            namespace["f"] = recompile(namespace["f"])
            return type.__new__(meta, name, bases, namespace)

    class C(metaclass=Meta):
        def f(self): return 1
    """
    assert refusal(tmp_path, source) is not None


def test_inline_source_compilation_is_the_same_transformation(tmp_path: Path) -> None:
    """Inlining the compiler into __new__ still replaces the method with compiled source."""
    source = """
    import inspect
    import textwrap

    class Meta(type):
        def __new__(meta, name, bases, namespace):
            method = namespace["f"]
            source = textwrap.dedent(inspect.getsource(method))
            code = compile(source, inspect.getsourcefile(method), "exec")
            rewritten = {}
            exec(code, method.__globals__, rewritten)
            namespace["f"] = rewritten[method.__name__]
            return type.__new__(meta, name, bases, namespace)

    class C(metaclass=Meta):
        def f(self): return 1
    """
    assert refusal(tmp_path, source) is not None


@pytest.mark.parametrize("inline", [False, True], ids=["called-compiler", "inline-compiler"])
def test_ast_rewriting_keeps_the_compiled_methods_origin(tmp_path: Path, inline: bool) -> None:
    """The retained hostile fixture parses and visits an AST before compiling it.

    Source provenance must survive that recognized path too. Both the called
    and inlined forms previously lost their tracing after an extraction.
    """
    compiler = textwrap.dedent("""
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        class Rewrite(ast.NodeTransformer):
            def visit_Constant(self, node):
                if node.value == 1:
                    return ast.copy_location(ast.Constant(value=2), node)
                return node
        tree = ast.fix_missing_locations(Rewrite().visit(tree))
        rewritten = {}
        exec(compile(tree, inspect.getsourcefile(method), "exec"), method.__globals__, rewritten)
        """).strip("\n")
    source = "import ast\nimport inspect\nimport textwrap\n"
    if inline:
        hook_body = (
            "method = namespace['f']\n"
            + compiler
            + "\nnamespace['f'] = rewritten[method.__name__]\n"
        )
    else:
        source += "def recompile(method):\n" + textwrap.indent(compiler, "    ")
        source += "\n    return rewritten[method.__name__]\n"
        hook_body = "namespace['f'] = recompile(namespace['f'])\n"
    source += "class Meta(type):\n    def __new__(meta, name, bases, namespace):\n"
    source += textwrap.indent(hook_body, "        ")
    source += "        return type.__new__(meta, name, bases, namespace)\n"
    source += "class C(metaclass=Meta):\n    def f(self): return 1\n"
    assert refusal(tmp_path, source) is not None


def test_source_used_only_as_a_compile_filename_is_not_instrumentation(tmp_path: Path) -> None:
    """Only compile's source argument becomes code; a diagnostic filename changes no method."""
    source = """
    import inspect

    class Base:
        def __init_subclass__(cls):
            compile("pass", inspect.getsource(cls.f), "exec")

    class C(Base):
        def f(self): return 1
    """
    assert refusal(tmp_path, source) is None


@pytest.mark.parametrize("forwards", [False, True], ids=["overridden", "super-forwarded"])
def test_subclass_hook_instrumentation_follows_actual_delegation(
    tmp_path: Path, forwards: bool
) -> None:
    """Base runs when creating Middle; it reaches C only if Middle forwards C to it."""
    call = "super().__init_subclass__()" if forwards else "pass"
    source = (
        "from typeguard import typechecked as checked\n"
        "class Base:\n"
        "    def __init_subclass__(cls):\n        checked(cls)\n"
        "class Middle(Base):\n"
        f"    def __init_subclass__(cls):\n        {call}\n"
        "class C(Middle):\n    def f(self): return 1\n"
    )
    assert refusal(tmp_path, source) == ("typeguard.typechecked" if forwards else None)


@pytest.mark.parametrize("start", ["Base", "Middle"], ids=["skip-instrumenter", "forward"])
def test_explicit_super_starts_after_the_named_class(tmp_path: Path, start: str) -> None:
    """super(Base, cls) skips Base's hook; super(Middle, cls) invokes it for C."""
    source = (
        "from typeguard import typechecked as checked\n"
        "class Base:\n"
        "    def __init_subclass__(cls):\n        checked(cls)\n"
        "class Middle(Base):\n"
        "    def __init_subclass__(cls):\n"
        f"        super({start}, cls).__init_subclass__()\n"
        "class C(Middle):\n    def f(self): return 1\n"
    )
    expected = "typeguard.typechecked" if start == "Middle" else None
    assert refusal(tmp_path, source) == expected


@pytest.mark.parametrize("forwards", [False, True], ids=["overridden", "super-forwarded"])
def test_metaclass_new_instrumentation_follows_actual_delegation(
    tmp_path: Path, forwards: bool
) -> None:
    """Calling type.__new__ directly bypasses Parent.__new__; super invokes it."""
    call = (
        "super().__new__(meta, name, bases, namespace)"
        if forwards
        else "type.__new__(meta, name, bases, namespace)"
    )
    source = (
        "from typeguard import typechecked as checked\n"
        "class Parent(type):\n"
        "    def __new__(meta, name, bases, namespace):\n"
        "        namespace['f'] = checked(namespace['f'])\n"
        "        return type.__new__(meta, name, bases, namespace)\n"
        "class Meta(Parent):\n"
        "    def __new__(meta, name, bases, namespace):\n"
        f"        return {call}\n"
        "class C(metaclass=Meta):\n    def f(self): return 1\n"
    )
    assert refusal(tmp_path, source) == ("typeguard.typechecked" if forwards else None)


@pytest.mark.parametrize("forwards", [False, True], ids=["overridden", "super-forwarded"])
def test_metaclass_init_instrumentation_follows_actual_delegation(
    tmp_path: Path, forwards: bool
) -> None:
    """An overridden metaclass initializer must be resolved separately from __new__."""
    call = "super().__init__(name, bases, namespace)" if forwards else "pass"
    source = (
        "from typeguard import typechecked as checked\n"
        "class Parent(type):\n"
        "    def __init__(cls, name, bases, namespace):\n        checked(cls)\n"
        "class Meta(Parent):\n"
        f"    def __init__(cls, name, bases, namespace):\n        {call}\n"
        "class C(metaclass=Meta):\n    def f(self): return 1\n"
    )
    assert refusal(tmp_path, source) == ("typeguard.typechecked" if forwards else None)


def test_a_returned_instrumentation_call_is_still_applied(tmp_path: Path) -> None:
    """A return expression evaluates its call just as an assignment or bare call does."""
    source = (
        "from typeguard import typechecked as checked\n"
        "class C:\n    def f(self): return 1\n"
        "def setup():\n    return checked(C)\n"
        "setup()\n"
    )
    assert refusal(tmp_path, source) == "typeguard.typechecked"


def test_a_comprehension_can_install_instrumented_methods(tmp_path: Path) -> None:
    """The target binds each method, and setattr installs the transformed result on C."""
    source = (
        "from typeguard import typechecked as checked\n"
        "class Base:\n"
        "    def __init_subclass__(cls):\n"
        "        [setattr(cls, name, checked(method)) for name, method in vars(cls).items()]\n"
        "class C(Base):\n    def f(self): return 1\n"
    )
    assert refusal(tmp_path, source) == "typeguard.typechecked"


def test_instrumenting_an_unrelated_function_does_not_instrument_the_argument(
    tmp_path: Path,
) -> None:
    """Reaching a transformer in observe is insufficient: it transforms unrelated, not cls."""
    source = """
    from typeguard import typechecked as checked

    def unrelated():
        return 1

    def observe(cls):
        checked(unrelated)
        print(cls)

    class Base:
        def __init_subclass__(cls):
            observe(cls)

    class C(Base):
        def f(self): return 1
    """
    assert refusal(tmp_path, source) is None


def test_reassigning_the_hook_receiver_ends_its_connection_to_the_class(tmp_path: Path) -> None:
    """The later call receives 123, not the constructed class; the original fact must be killed."""
    source = """
    from typeguard import typechecked as checked

    class Base:
        def __init_subclass__(cls):
            cls = 123
            checked(cls)

    class C(Base):
        def f(self): return 1
    """
    assert refusal(tmp_path, source) is None
