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

"""Wiring the annotation rules into a proposal, and verifying the result.

The engine asks annotations.py to copy what the call sites declare and to
infer the rest through the project's type checker, decides whether the
generated code is to be type-checked, supplies generic candidates before the
fallback variants (every annotation Any, then none), and compares messages before and
after a change. Oracle inference and verification run only when the complete
original project is clean; existing errors and checker infrastructure failures
refuse application with distinct diagnostics.
"""

from __future__ import annotations

import ast
from collections import Counter
import configparser
import copy
import dataclasses
from pathlib import Path
import re

from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
from .annotations import (
    OLDEST_PYTHON,
    ApplySite,
    CallSite,
    PythonVersion,
    annotate_helper,
    call_in_statement,
    complete_with_any,
    infer_missing_annotations,
    respell_bare,
    sites_use_annotations,
    typing_imports_needed,
    qualified_names_in_annotations,
    shorten_qualified_names,
    defers_annotations,
    evaluated_syntax,
    written_for_python,
    _import_bound_names,
    _defined_names,
)
from .exceptions import CheckerUnavailableError, ProjectScanLimitError, RefactoringError
from .models import FunctionNode, RefactoringProposal, span_contains
from ..diagnostics import TYPES
from ..checker_project import _read_json_config
from ..project_layout import find_project_root, load_pyproject
from ..type_inference import (
    CheckFailure,
    TypeDiagnostic,
    TypeOracle,
    _configured_root,
    _mypy_config,
    checker_module_name,
    holds_warm_state,
    start_cold,
)

from .engine_state import EngineState
from ..source_text import read_source, source_lines, try_read_source
from .function_index import FunctionIndex
from .generic_annotations import MethodContext, generic_helpers
from .type_bindings import CheckerImports, ModuleNames
from .program_imports import ProgramImports

UNTYPED_REMEDY = "rerun with --no-types (library: type_oracle=None, annotate_helpers=False)."
"""The way out of every refusal to verify: the one thing such a user can act on."""


def python_lower_bound(specifier: str) -> Optional[PythonVersion]:
    """The oldest Python a version requirement admits, to the minor version; None if unbounded.

    PEP 440 clauses, comma-separated, as ``requires-python`` spells them, and
    Poetry's ``^3.9`` and ``~3.9``: ``>=``, ``~=``, ``==``, ``===``, ``^`` and
    ``~`` bound from below at their version, ``>`` at the same minor version
    (``>3.8`` admits 3.8.1), and the tightest bound wins. ``<``, ``<=`` and
    ``!=`` bound nothing below. Anything unreadable makes the whole answer
    None, which the caller takes as the oldest Python there is.
    """
    bounds: List[PythonVersion] = []
    for clause in (part.strip() for part in specifier.split(",")):
        if not clause:
            continue
        match = _VERSION_CLAUSE.fullmatch(clause)
        if match is None:
            return None
        if match.group("operator") in {"<", "<=", "!="}:
            continue
        bounds.append((int(match.group("major")), int(match.group("minor") or 0)))
    return max(bounds) if bounds else None


_VERSION_CLAUSE = re.compile(
    r"(?P<operator>===|==|~=|>=|<=|!=|>|<|\^|~)\s*v?(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?(?:\.(?:\d+|\*))*(?:[a-z]+\d*)?(?:\.\*)?"
)


def _table(document: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    """``document[keys[0]][keys[1]]...``, or an empty table where any step is not one."""
    node: object = document
    for key in keys:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _checker_python(value: object) -> Optional[PythonVersion]:
    """``python_version``/``pythonVersion`` as a version: ``"3.9"``, or TOML's ``3.9``."""
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", str(value).strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _ini_option(path: Path, section: str, option: str) -> Optional[str]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return None
    return parser.get(section, option, fallback=None)


def _checker_targets(path: Path) -> List[PythonVersion]:
    """The Python versions the project's mypy and pyright configurations target."""
    targets: List[Optional[PythonVersion]] = []
    mypy_root = _configured_root(path, "mypy")
    config = _mypy_config(mypy_root) if mypy_root is not None else None
    if config is not None and mypy_root is not None:
        if config.endswith("pyproject.toml"):
            mypy = _table(load_pyproject(mypy_root), "tool", "mypy")
            targets.append(_checker_python(mypy.get("python_version", "")))
        else:
            targets.append(_checker_python(_ini_option(Path(config), "mypy", "python_version")))
    pyright_root = _configured_root(path, "pyright")
    if pyright_root is not None:
        settings: Mapping[str, object]
        try:
            settings = (
                _read_json_config(pyright_root / "pyrightconfig.json")
                if (pyright_root / "pyrightconfig.json").is_file()
                else _table(load_pyproject(pyright_root), "tool", "pyright")
            )
        except ValueError:
            settings = {}  # A configuration pyright rejects refuses the run elsewhere.
        targets.append(_checker_python(settings.get("pythonVersion", "")))
    return [target for target in targets if target is not None]


def declared_oldest_python(path: Path) -> Optional[PythonVersion]:
    """The oldest Python the project around ``path`` says it supports, or None when it says nothing.

    ``requires-python`` first (``[project]`` in pyproject.toml, else setup.cfg's
    ``python_requires``, else Poetry's ``python`` dependency), since that is the
    promise the package makes to whoever installs it. Failing that, the older
    of the versions mypy and pyright are configured to check for: a project
    whose checker targets 3.9 means its code to run there.
    """
    root = find_project_root(path)
    pyproject = load_pyproject(root)
    requirement: object = _table(pyproject, "project").get("requires-python")
    if not isinstance(requirement, str) and (root / "setup.cfg").is_file():
        requirement = _ini_option(root / "setup.cfg", "options", "python_requires")
    if not isinstance(requirement, str):
        requirement = _table(pyproject, "tool", "poetry", "dependencies").get("python")
    if isinstance(requirement, str):
        bound = python_lower_bound(requirement)
        if bound is not None:
            return bound
    targets = _checker_targets(path)
    return min(targets) if targets else None


class HelperAnnotationWiring(EngineState):
    """Helper AnnotationWiring methods of the engine; see the module docstring."""

    # The oldest Python each project root declares, read once per engine.
    _declared_pythons: Mapping[str, Optional[PythonVersion]] = {}

    def begin_refactoring_run(self, file_paths: Sequence[str]) -> None:
        """Establish a new run's original project before inference or changes.

        Fixed-point drivers call this automatically. Direct library callers
        may call it to start another run on the same engine; otherwise their
        applications share one lazily initialized run. The supplied paths
        anchor the oracle's complete project snapshot, including unchanged
        consumers. This never closes or replaces the caller-owned oracle.
        """
        self._type_run_oracle = self.type_oracle
        self._type_run_baseline = None
        self._analysis_paths = tuple(file_paths)
        self._output_origin = None
        self.import_graph.begin_run()
        self._ensure_type_checking(file_paths)

    def _ensure_type_checking(self, file_paths: Sequence[str]) -> None:
        """Check once; neither existing errors nor checker failure permit application."""
        if self._type_run_oracle is None:
            return
        if self._type_run_baseline is None and file_paths:
            originals: Dict[str, str] = {}
            for path in dict.fromkeys(file_paths):
                source = self._read_source(path)
                if source is None:
                    self._type_run_baseline = CheckFailure(f"Cannot read original source: {path}")
                    break
                originals[path] = source
            else:
                self._type_run_baseline = self._type_run_oracle.check_project(originals)
                if not isinstance(self._type_run_baseline, CheckFailure):
                    for diagnostic in self._type_run_baseline.errors:
                        TYPES.debug("original error in %s: %s", diagnostic.path, diagnostic.message)
        if isinstance(self._type_run_baseline, CheckFailure):
            # A checker that cannot run at all -- a config naming a Python
            # version it has dropped, a tree it cannot build -- leaves the same
            # user in the same place as one reporting errors, and said nothing
            # about how to get out of it. Voluptuous and Lark, whose configs
            # ask mypy 1.19 for Python 3.9 and 3.8, are both this.
            raise RefactoringError(
                f"Original project type check failed: {self._type_run_baseline.reason}\n"
                f"{UNTYPED_REMEDY}"
            )
        if self._type_run_baseline is not None and self._type_run_baseline.errors:
            errors = self._type_run_baseline.errors
            details = "\n".join(f"  {error.path}: {error.message}" for error in errors[:3])
            if len(errors) > 3:
                details += (
                    f"\n  ... and {len(errors) - 3} more "
                    "(TOWEL_DEBUG_TYPES=1 shows all diagnostics)."
                )
            raise RefactoringError(
                f"Original project check reported {len(errors)} type error(s):\n{details}\n"
                f"Fix the existing errors or {UNTYPED_REMEDY}"
            )

    def _active_type_oracle(self) -> Optional[TypeOracle]:
        """The caller's oracle only when this run's original check completed cleanly."""
        if self._type_run_oracle is None:
            return None
        self._ensure_type_checking(())
        if self._type_run_baseline is None:
            raise RefactoringError("The original project type check has not run")
        return self._type_run_oracle

    def _with_helper_annotations(
        self, proposal: RefactoringProposal, functions: FunctionIndex
    ) -> RefactoringProposal:
        """The proposal with its helper annotated from what the call sites declare.

        Runs after every verification, since annotations play no part in the
        instantiation check, and after clustering, which compares helper
        bodies structurally.
        """
        sites: List[CallSite] = []
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            call = call_in_statement(replacement.node, proposal.extracted_function.name)
            function = functions.innermost_at(file_path, replacement.line_range)
            module = function.scope_analyzer.analyzed_tree if function is not None else None
            if call is None or function is None or not isinstance(module, ast.Module):
                return proposal
            sites.append(
                CallSite(
                    statement=replacement.node,
                    call=call,
                    function=function.node,
                    module=module,
                    file_path=file_path,
                )
            )
        annotated = annotate_helper(
            proposal.extracted_function, sites, proposal.file_path, proposal.return_variables
        )
        return dataclasses.replace(
            proposal,
            extracted_function=annotated,
            wants_type_inference=sites_use_annotations(sites),
        )

    def _origin_of(self, path: str) -> str:
        """``path`` as it stood in the project whose names the checker answers with.

        A driver writing into an output directory checks the copy under the
        input project's module names, so a name it answers with is resolved
        against the input's layout rather than the copy's, where the top
        package is the output directory and nothing would be found.
        """
        origin = self._output_origin
        if origin is None:
            return path
        source, destination = (root.resolve() for root in origin)
        absolute = Path(path).resolve()
        if absolute == destination:
            return str(source)
        if absolute.is_relative_to(destination):
            return str(source / absolute.relative_to(destination))
        return path

    def _shorten_unreachable_names(
        self, proposal: RefactoringProposal, host: Optional[ast.Module]
    ) -> Tuple[Tuple[str, str], ...]:
        """Give each qualified name the host cannot reach a spelling it can, and an import.

        A checker answers with a whole path. Written into a module that never
        imports that submodule it is not a name at all, however plainly its
        head is bound: the package object carries no such attribute. Whether a
        path is reachable is not decidable from the syntax, so the test is
        whether the short name is free here. When it is, the short name is used
        and the import stated under ``TYPE_CHECKING``, spelled as the program's
        own imports show the host can import that module
        (``ImportModel.split_qualified``, then ``ImportModel.spelling``). When
        the name is taken, or no module of the project is known to own it, or
        the host is not known to be able to import that module, the path is
        left as the checker wrote it and the project check decides.

        Such an import never runs, so it changes nothing a program does, and
        it is written whether or not helpers are shared across modules; the
        program's imports are read for it only when a name needs one. Where
        they cannot be read at all, every path is left as written.
        """
        imports: List[Tuple[str, str]] = []
        shortened: Dict[str, str] = {}
        bound = (_import_bound_names(host) | _defined_names(host)) if host is not None else set()
        importer = Path(proposal.file_path)
        program: Optional[ProgramImports] = None
        for dotted in qualified_names_in_annotations(proposal.extracted_function):
            name = dotted.rsplit(".", 1)[-1]
            if name in bound or name in shortened.values():
                continue
            if program is None:
                try:
                    program = self.import_graph.program_for(importer)
                except ProjectScanLimitError:
                    if self.cross_module_helpers:
                        raise
                    break  # Nothing can be spelled; every path stays as written.
            split = program.model.split_qualified(dotted)
            if split is None:
                continue  # No trusted name of the project owns it; leave it written out.
            module_file, _, qualname = split
            if qualname != name:
                continue  # A nested name needs its owner in scope, not just itself.
            spelling = program.spelling(importer, module_file)
            if spelling is None:
                continue  # The host is not known to be able to import it; leave it written out.
            shortened[dotted] = name
            imports.append((spelling.module, name))
        if shortened:
            shorten_qualified_names(
                proposal.extracted_function, shortened, quote=not defers_annotations(host)
            )
        return tuple(imports)

    @staticmethod
    def _receiver_name(proposal: RefactoringProposal) -> Optional[str]:
        """The parameter a method dispatches on, whose type its class already fixes."""
        if proposal.insert_into_class is None or proposal.method_kind == "staticmethod":
            return None
        return proposal.method_param_name or (
            "cls" if proposal.method_kind == "classmethod" else "self"
        )

    def _infer_helper_annotations(self, proposal: RefactoringProposal) -> None:
        """Finish the helper's annotations in place when the proposal is applied.

        The type inferrer, when there is one, fills what the copied
        annotations could not; then a helper that carries any annotation gets
        ``Any`` on whatever is still bare, so its signature is complete. Runs
        once per applied proposal, on the files as they stand, so the cost is
        one incremental type-check per application rather than one per
        candidate. Last, an annotation the project's oldest Python could not
        evaluate is written as a string (see :meth:`_oldest_python_for`).
        """
        if not proposal.wants_type_inference or proposal.reused_function is not None:
            return
        self._complete_helper_annotations(proposal)
        host = self._parsed_host(proposal.file_path)
        proposal.extracted_function = written_for_python(
            proposal.extracted_function, host, self._oldest_python_for(proposal.file_path, host)
        )

    def _oldest_python_for(self, file_path: str, host: Optional[ast.Module]) -> PythonVersion:
        """The oldest Python the helper's module has to import on.

        What the project declares (:func:`declared_oldest_python`), or, where it
        declares nothing, the oldest Python the syntax could need. A module
        that already evaluates younger syntax on every import needs that Python
        anyway, so its own evidence raises the floor
        (:func:`~towel.unification.annotations.evaluated_syntax`).
        """
        root = str(find_project_root(Path(self._origin_of(file_path))))
        if root not in self._declared_pythons:
            self._declared_pythons = {
                **self._declared_pythons,
                root: declared_oldest_python(Path(self._origin_of(file_path))),
            }
        declared = self._declared_pythons[root] or OLDEST_PYTHON
        return max(declared, evaluated_syntax(host)) if host is not None else declared

    def _complete_helper_annotations(self, proposal: RefactoringProposal) -> None:
        """Copied annotations respelled, the rest inferred or completed with ``Any``."""
        module_level = proposal.insert_into_class is None and proposal.insert_into_function is None
        host_source = self._read_source(proposal.file_path)
        bare_ok = self.placeable_after(host_source) if module_level and host_source else set()
        host = self._parsed_host(proposal.file_path)
        receiver = self._receiver_name(proposal)
        oracle = self._active_type_oracle()
        if oracle is None:
            respelled = respell_bare(proposal.extracted_function, host, bare_ok)
            completed = complete_with_any(respelled, host, receiver)
            proposal.extracted_function = completed.helper
            proposal.required_imports = completed.required_imports
            return
        sites = self._annotation_sites(proposal)
        if not sites:
            return
        inferred = infer_missing_annotations(
            respell_bare(proposal.extracted_function, host, bare_ok),
            sites,
            proposal.file_path,
            proposal.return_variables,
            oracle,
            bare_ok,
            receiver,
        )
        completed = complete_with_any(respell_bare(inferred.helper, host, bare_ok), host, receiver)
        proposal.extracted_function = completed.helper
        proposal.required_imports = tuple(
            dict.fromkeys(inferred.required_imports + completed.required_imports)
        )
        proposal.type_checking_imports = self._shorten_unreachable_names(proposal, host)

    def _annotation_sites(self, proposal: RefactoringProposal) -> List[ApplySite]:
        """Keep the current source and return context of each replacement together."""
        sites: List[ApplySite] = []
        sources: Dict[str, str] = {}
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            call = call_in_statement(replacement.node, proposal.extracted_function.name)
            if call is None:
                return []
            source = sources.get(file_path)
            if source is None:
                source = read_source(file_path)
                sources[file_path] = source
            lines = source_lines(source)
            start_line, end_line = replacement.line_range
            if not 1 <= start_line <= len(lines):
                return []
            sites.append(
                ApplySite(
                    file_path=file_path,
                    source=source,
                    start_line=start_line,
                    end_line=end_line,
                    indent=self._get_indent(lines[start_line - 1]),
                    statement=replacement.node,
                    call=call,
                    declared_return=self._declared_return_at(source, start_line),
                )
            )
        return sites

    def _generic_helper_variants(
        self, proposal: RefactoringProposal
    ) -> Iterator[RefactoringProposal]:
        """Candidate parametric signatures, still requiring complete project verification."""
        if (
            not proposal.wants_type_inference
            or proposal.reused_function is not None
            or proposal.insert_into_function is not None
        ):
            return
        oracle = self._active_type_oracle()
        if oracle is None:
            return
        source = self._read_source(proposal.file_path)
        if source is None:
            return
        method = (
            MethodContext(
                proposal.insert_into_class,
                proposal.method_kind or "instance",
                proposal.method_param_name
                or ("cls" if proposal.method_kind == "classmethod" else "self"),
            )
            if proposal.insert_into_class is not None
            else None
        )
        for candidate in generic_helpers(
            proposal.extracted_function,
            self._annotation_sites(proposal),
            proposal.file_path,
            source,
            proposal.return_variables,
            oracle,
            method=method,
            module_names=self._module_names_for(proposal),
            checker_imports=self._checker_imports_for(proposal),
        ):
            yield dataclasses.replace(
                proposal,
                extracted_function=candidate.helper,
                required_imports=candidate.required_imports,
                type_checking_imports=candidate.type_checking_imports,
                helper_type_declarations=candidate.declarations,
            )

    def _module_names_for(self, proposal: RefactoringProposal) -> ModuleNames:
        """The absolute name of each module, as the checker writes it in the types it reveals.

        The name the program's own imports give the module, and, for a module
        no import names (a script, a package only ever imported relatively),
        the one mypy was given for it: its ``__init__`` chain in the project
        the checker reads (:func:`~towel.type_inference.checker_module_name`).
        """
        try:
            program: Optional[ProgramImports] = self.import_graph.program_for(
                Path(proposal.file_path)
            )
        except ProjectScanLimitError:
            program = None

        def module_name(path: str) -> Optional[str]:
            named = program.module_name(Path(path)) if program is not None else None
            return named or checker_module_name(Path(self._origin_of(path)))

        return module_name

    def _checker_imports_for(self, proposal: RefactoringProposal) -> Optional[CheckerImports]:
        """How the helper's module may import a class for the checker, as the program shows.

        The rule :meth:`_shorten_unreachable_names` follows for the ordinary
        signature: a top-level class of a module of the project, imported the
        way the program's own imports show the host can import that module.
        """
        importer = Path(proposal.file_path)
        try:
            program = self.import_graph.program_for(importer)
        except ProjectScanLimitError:
            return None

        def checker_import(qualified: str) -> Optional[Tuple[str, str]]:
            split = program.model.split_qualified(qualified)
            if split is None or "." in split[2]:
                return None  # No module of the project owns it, or it is nested.
            spelling = program.spelling(importer, split[0])
            return None if spelling is None else (spelling.module, split[2])

        return checker_import

    @staticmethod
    def _helper_uses_any(proposal: RefactoringProposal) -> bool:
        """Whether the ordinary signature has already lost part of its type information."""
        helper = proposal.extracted_function
        annotations = [arg.annotation for arg in helper.args.posonlyargs + helper.args.args]
        annotations.append(helper.returns)
        for annotation in annotations:
            if annotation is None:
                return True
            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                try:
                    annotation = ast.parse(annotation.value, mode="eval").body
                except SyntaxError:
                    return True
            if any(
                isinstance(node, ast.Name)
                and node.id == "Any"
                or isinstance(node, ast.Attribute)
                and node.attr == "Any"
                for node in ast.walk(annotation)
            ):
                return True
        return False

    @staticmethod
    def _annotation_names(helper: ast.FunctionDef) -> Set[str]:
        """Names the helper's unquoted annotations refer to."""
        names: Set[str] = set()
        annotations = [arg.annotation for arg in helper.args.posonlyargs + helper.args.args] + [
            helper.returns
        ]
        for annotation in annotations:
            if annotation is not None and not isinstance(annotation, ast.Constant):
                names |= {n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)}
        return names

    @staticmethod
    def _read_source(file_path: str) -> Optional[str]:
        return try_read_source(file_path)

    @staticmethod
    def _declared_return_at(source: str, line: int) -> Optional[ast.expr]:
        """The return annotation of the innermost function containing ``line``."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        innermost: Optional[FunctionNode] = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and span_contains(node, (line, line))
                and (innermost is None or node.lineno > innermost.lineno)
            ):
                innermost = node
        return innermost.returns if innermost is not None else None

    def _parsed_host(self, file_path: str) -> Optional[ast.Module]:
        source = try_read_source(file_path)
        if source is None:
            return None
        try:
            return self._parse_source(source)
        except SyntaxError:
            return None

    @staticmethod
    def _helper_has_annotations(proposal: RefactoringProposal) -> bool:
        """Whether a new helper has annotations that can be weakened on retry."""
        helper = proposal.extracted_function
        return helper.returns is not None or any(
            arg.annotation is not None for arg in helper.args.posonlyargs + helper.args.args
        )

    def _with_every_annotation_any(self, proposal: RefactoringProposal) -> RefactoringProposal:
        """The proposal with every helper annotation replaced by ``Any``.

        Only the helper is copied: the replacements are shared with the
        proposal, and materialization copies each one before touching it.
        """
        helper = copy.deepcopy(proposal.extracted_function)
        variant = dataclasses.replace(
            proposal, extracted_function=helper, helper_type_declarations=()
        )
        for arg in helper.args.posonlyargs + helper.args.args:
            arg.annotation = ast.Name(id="Any", ctx=ast.Load())
        helper.returns = ast.Name(id="Any", ctx=ast.Load())
        variant.required_imports = typing_imports_needed(
            helper, self._parsed_host(variant.file_path)
        )
        return variant

    @staticmethod
    def _without_annotations(proposal: RefactoringProposal) -> RefactoringProposal:
        """The proposal with the helper unannotated; see ``_with_every_annotation_any``."""
        helper = copy.deepcopy(proposal.extracted_function)
        variant = dataclasses.replace(
            proposal, extracted_function=helper, helper_type_declarations=()
        )
        for arg in helper.args.posonlyargs + helper.args.args:
            arg.annotation = None
        helper.returns = None
        variant.required_imports = ()
        return variant

    def confirm_run_with_a_cold_checker(self, file_paths: Sequence[str]) -> None:
        """Check the finished project once more, with no state the run kept.

        A language server is asked about a candidate and answers when it has
        gone quiet. Each answer is guarded by a marker it must publish first,
        so silence alone is never read as a verdict, but that guards the
        server's start and not every instant of its reply. mypy keeps state as
        well: an incremental cache some of whose entries were written from text
        that never reached disk, and a scan of what imports the change that has
        to follow every file the run rewrites. Either, wrong at one instant,
        calls a broken candidate clean, and nothing later in the run would ask
        again.

        A checker started from nothing shares none of those assumptions, so
        one run of it over the finished project turns any residue of that kind
        from a silent wrong answer into a loud one. It costs a single cold
        check per run, about what the run's own baseline cost, and is done
        whenever a run applied anything, since that is exactly when the promise
        being kept is that the project still checks. The drivers ask this of
        the private stage the run refactored, before anything is published, so
        a refusal here leaves the project and the output as they were.
        """
        oracle = self._type_run_oracle
        if oracle is None or not holds_warm_state(oracle):
            return
        sources: Dict[str, str] = {}
        for path in dict.fromkeys(file_paths):
            source = self._read_source(path)
            if source is None:
                return  # A file that cannot be read is reported by the run itself.
            sources[path] = source
        # The same oracle, so the run's own relocation and exclusions still
        # apply; only the warm state goes.
        start_cold(oracle)
        result = oracle.check_project(sources)
        if isinstance(result, CheckFailure):
            # This check exists to make a wrong answer loud; a check that could
            # not run has not confirmed anything, and saying so quietly would
            # be the same silence it was built to remove.
            raise RefactoringError(
                "The finished project could not be confirmed by a checker started from "
                f"nothing: {result.reason}\nNothing was written."
            )
        if result.errors:
            details = "\n".join(f"  {error.path}: {error.message}" for error in result.errors[:3])
            raise RefactoringError(
                f"The finished project reports {len(result.errors)} type error(s) that the "
                f"checker did not report while the run was in progress:\n{details}\n"
                "This is a defect in Towel's verification, not in the project; please report "
                "it. Nothing was written."
            )

    def _new_type_errors(self, modified_files: Dict[str, str]) -> Tuple[TypeDiagnostic, ...]:
        """What the run's clean project, unchanged consumers included, would now report."""
        oracle = self._active_type_oracle()
        if oracle is None:
            raise RefactoringError("Type checking was requested without a type oracle")
        after = oracle.check_project(modified_files)
        if isinstance(after, CheckFailure):
            raise CheckerUnavailableError(f"Prospective project type check failed: {after.reason}")
        for diagnostic, count in Counter(after.errors).items():
            TYPES.debug("new error x%d in %s: %s", count, diagnostic.path, diagnostic.message)
        if after.errors:
            # Counted so a proposal no variant of which survives is reported as
            # the checker's refusal, not as something that could not be rendered.
            self._checker_refusals += 1
        return after.errors
