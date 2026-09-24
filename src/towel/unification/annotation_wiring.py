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
generated code is to be type-checked, orders the signatures a typed proposal
is tried with (:meth:`HelperAnnotationWiring._annotation_ladder`; what each
rung writes is in annotation_ladder.py), and compares messages before and
after a change. Oracle inference and verification start from a check of the
complete original project: the errors it reports are left as they are, and a
change is rejected only for an error they do not account for
(``towel.type_baseline``). A checker that cannot run at all refuses the run.
"""

from __future__ import annotations

import ast
from collections import Counter
import configparser
import copy
import dataclasses
import fnmatch
import hashlib
import os
from pathlib import Path
import re

from typing import Dict, FrozenSet, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
from ..canonical_ast import canonical_dump
from .annotation_ladder import (
    Hearing,
    Judge,
    Rejection,
    Unanswerable,
    declarations_leave_their_class,
    drop_unbound_variables,
    method_at,
    narrowing_needed_in_thunk,
    narrowing_refused_in_thunk,
    narrowing_the_call_cannot_carry,
    partial_type_passed,
    self_as_type_variable,
    targeted_any,
    unannotated_function,
    used_imports,
    without_quoted_none,
)
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
from .exceptions import (
    CheckerUnavailableError,
    ProjectScanLimitError,
    RefactoringError,
    UncheckedCodeError,
    Untypeable,
    UnverifiableChangeError,
)
from .models import FunctionNode, RefactoringProposal, span_contains
from ..diagnostics import LOG, TYPES
from ..checker_project import _read_json_config
from ..project_layout import find_project_root, load_pyproject, package_chain
from ..type_baseline import (
    CheckedChange,
    KnownErrors,
    files_where_names_are_any,
    names_any_warning,
    pre_existing_summary,
    resolved_path,
    unchanged_lines,
    unlooked_warning,
)
from ..reachability import PROBE, Place, probe_plan
from ..type_inference import (
    CheckFailure,
    CombinedOracle,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    TypeDiagnostic,
    TypeOracle,
    _configured_root,
    _module_name_and_root,
    _mypy_config,
    _RelocatedOracle,
    checker_module_name,
    checks_in_turn,
    holds_warm_state,
    reveal_by_each,
    start_cold,
)

from .engine_state import EngineState
from ..source_text import read_source, source_lines, try_read_source
from .function_index import FunctionIndex
from .generic_annotations import MethodContext, generic_helpers
from .type_bindings import CheckerImports, ModuleNames
from .program_imports import ProgramImports

UNTYPED_REMEDY = "rerun with --no-types (library: type_oracle=None, annotate_helpers=False)."
"""The way out when the checker cannot run at all: the one thing such a user can act on."""


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


_LADDER_FLAGS = ("disallow_untyped_defs", "warn_return_any", "check_untyped_defs")
"""The mypy options that decide what the ladder can learn before checking; ``strict`` sets all three."""


def _boolean(value: object) -> Optional[bool]:
    """A configuration value as a boolean: TOML's own, or an ini file's ``True``/``false``/``1``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


_Section = Mapping[str, object]


def _mypy_sections(config: Path) -> Tuple[_Section, List[Tuple[Tuple[str, ...], _Section]]]:
    """The global mypy options, and each per-module section with the module patterns it names."""
    if config.name == "pyproject.toml":
        table = _table(load_pyproject(config.parent), "tool", "mypy")
        entries = table.get("overrides", [])
        sections: List[Tuple[Tuple[str, ...], _Section]] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            modules = entry.get("module")
            patterns = (
                (modules,)
                if isinstance(modules, str)
                else (
                    tuple(m for m in modules if isinstance(m, str))
                    if isinstance(modules, list)
                    else ()
                )
            )
            sections.append((patterns, entry))
        return table, sections
    parser = configparser.ConfigParser()
    try:
        parser.read(config, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return {}, []
    options: _Section = dict(parser["mypy"]) if parser.has_section("mypy") else {}
    return options, [
        (
            tuple(pattern.strip() for pattern in section[len("mypy-") :].split(",")),
            dict(parser[section]),
        )
        for section in parser.sections()
        if section.startswith("mypy-")
    ]


def _names_module(pattern: str, module: str) -> bool:
    """Whether a mypy per-module pattern names ``module``: ``pkg``, ``pkg.*`` or ``pkg.*.mod``."""
    if pattern.endswith(".*") and "*" not in pattern[:-2]:
        prefix = pattern[:-2]
        return module == prefix or module.startswith(prefix + ".")
    if "*" in pattern:
        return fnmatch.fnmatchcase(module, pattern) or fnmatch.fnmatchcase(module, pattern[:-2])
    return module == pattern


def mypy_ladder_flags(path: Path) -> Dict[str, bool]:
    """Each of ``_LADDER_FLAGS`` as the project's mypy applies it to ``path``.

    What the global section says, ``strict`` setting both where a flag is not
    given, then every per-module section whose pattern names the module, in
    the order the file gives them. A project without a mypy configuration has
    mypy's defaults, which set neither.
    """
    root = _configured_root(path, "mypy")
    config = _mypy_config(root) if root is not None else None
    if config is None:
        return {flag: False for flag in _LADDER_FLAGS}
    options, overrides = _mypy_sections(Path(config))
    normalized = {str(key).replace("-", "_"): value for key, value in options.items()}
    strict = _boolean(normalized.get("strict")) or False
    flags: Dict[str, bool] = {}
    for flag in _LADDER_FLAGS:
        given = _boolean(normalized.get(flag))
        flags[flag] = strict if given is None else given
    module = _module_name_and_root(path)[0]
    for patterns, section in overrides:
        if not any(_names_module(pattern, module) for pattern in patterns):
            continue
        for key, value in section.items():
            flag = str(key).replace("-", "_")
            given = _boolean(value)
            if flag in flags and given is not None:
                flags[flag] = given
    return flags


def verifies_with_mypy(oracle: Optional[TypeOracle]) -> bool:
    """Whether mypy is among the checkers ``oracle`` verifies with."""
    if isinstance(oracle, MypyInferrer):
        return True
    if isinstance(oracle, _RelocatedOracle):
        return verifies_with_mypy(oracle.inner)
    if isinstance(oracle, CombinedOracle):
        return any(verifies_with_mypy(checker) for checker in oracle.checkers)
    return False


@dataclasses.dataclass(frozen=True)
class _LadderPolicy:
    """What the project's checker settles in advance about the fallback rungs of one module."""

    annotations_required: bool = False
    """mypy's ``disallow_untyped_defs``: an unannotated helper is refused on its own definition."""
    returning_any_refused: bool = False
    """mypy's ``warn_return_any``: a helper returning ``Any`` is refused wherever its value is returned."""
    untyped_bodies_checked: bool = False
    """mypy's ``check_untyped_defs``: the body of an unannotated function is checked too."""
    mypy: bool = False
    """Whether mypy verifies at all; every other field is False when it does not."""


@dataclasses.dataclass(frozen=True)
class _RefusedCheck:
    """A project check that refused a variant, and what its verdict depended on."""

    errors: Tuple[TypeDiagnostic, ...]
    helper_name: str
    depends_on: Tuple[Tuple[str, str], ...]
    """(path, digest) of every file the refusal could depend on, the variant's own as they stand."""
    known: str
    """What the run already counted as the project's own errors in those files (``type_baseline``)."""


_REFUSED_CHECKS_KEPT = 512
"""Refusals a run remembers; a variant refused earlier is heard at a rehearing, not before."""
_DEPENDENCIES_FOLLOWED = 4096
"""Past this many files a refusal's dependencies are not followed and it is not remembered."""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _variant_key(files: Mapping[str, str], helper_name: str) -> str:
    """What a rendered variant is, apart from the name its helper was given this time.

    A helper's generated name is allocated per attempt and advances with every
    application, so a variant rendered again after unrelated changes differs
    only there (packaging: ``__extracted_func_6`` heard first, ``_9`` again).
    """
    name = re.compile(rf"(?<!\w){re.escape(helper_name)}(?!\w)")
    parts: List[str] = []
    for path in sorted(files):
        parts.extend((os.path.realpath(path), name.sub(_HELPER_PLACEHOLDER, files[path])))
    return _digest(_SEPARATOR.join(parts))


_HELPER_PLACEHOLDER = "<helper>"
"""Stands for the helper's name in a variant's key; no identifier can be spelled so."""
_SEPARATOR = "\0"


def _module_imports(tree: ast.Module) -> List[Tuple[int, str, Tuple[str, ...]]]:
    """``(level, module, names)`` for every import anywhere in ``tree``, conditional or not."""
    found: List[Tuple[int, str, Tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((0, alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.level, node.module or "", tuple(a.name for a in node.names)))
    return found


def _imported_files(path: Path, tree: ast.Module, roots: Sequence[Path]) -> Set[Path]:
    """The source files under ``roots`` that ``tree``'s imports may read, package initializers included.

    Resolved generously -- every candidate location of every name, submodule
    or not -- because a missed dependency could only make a remembered refusal
    outlive the change that answers it, and an extra one only makes it
    forgotten sooner.
    """
    found: Set[Path] = set()

    def modules(base: Path, dotted: str) -> None:
        directory = base
        for part in [part for part in dotted.split(".") if part]:
            initializer = directory / "__init__.py"
            if initializer.is_file():
                found.add(initializer)
            candidate = directory / f"{part}.py"
            if candidate.is_file():
                found.add(candidate)
            directory = directory / part
        initializer = directory / "__init__.py"
        if initializer.is_file():
            found.add(initializer)

    for level, module, names in _module_imports(tree):
        if level:
            base = path.parent
            for _ in range(level - 1):
                base = base.parent
            bases = [base]
        else:
            bases = list(roots)
        for base in bases:
            modules(base, module)
            for name in names:
                modules(base, f"{module}.{name}" if module else name)
    return found


class HelperAnnotationWiring(EngineState):
    """Helper AnnotationWiring methods of the engine; see the module docstring."""

    # The oldest Python each project root declares, read once per engine.
    _declared_pythons: Mapping[str, Optional[PythonVersion]] = {}
    # What the project's mypy settles about the fallback rungs, per module, once per run.
    _ladder_policies: Mapping[str, _LadderPolicy] = {}
    # Project checks that refused a variant this run, by what the variant renders
    # to (``_variant_key``); see ``_project_errors``.
    _refused_checks: Mapping[str, _RefusedCheck] = {}

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
        self._ladder_policies = {}
        self._refused_checks = {}
        self._type_known = KnownErrors()
        self._type_checked = None
        self._type_names_any = {}
        self._type_unlooked = {}
        self._analysis_paths = tuple(file_paths)
        self._output_origin = None
        self.import_graph.begin_run()
        self._ensure_type_checking(file_paths)

    def _ensure_type_checking(self, file_paths: Sequence[str]) -> None:
        """Check the original project once; only a checker that cannot run refuses the run.

        The errors that check reports are what every later check is compared
        with (``towel.type_baseline``): the run leaves them as they are, and
        rejects a change only for an error they do not account for. It used to
        refuse any project that reported one: of 20 corpus projects, all 17
        that type-check pass their own check as their CI runs it, and Towel's
        check was clean for 6, its errors lying in tests, benchmarks and docs
        the CI never checks. They are reported before anything else happens,
        with the files where they leave names the checker cannot type
        (``_report_pre_existing``).
        """
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
                baseline = self._type_run_oracle.check_project(originals)
                self._type_run_baseline = baseline
                if not isinstance(baseline, CheckFailure):
                    for diagnostic in baseline.errors:
                        TYPES.debug("original error in %s: %s", diagnostic.path, diagnostic.message)
                    self._type_known = KnownErrors.of(
                        baseline.errors,
                        self._where_checked,
                        texts={self._where_checked(path): text for path, text in originals.items()},
                    )
                    self._type_names_any = files_where_names_are_any(self._type_known.errors)
                    self._report_pre_existing(list(originals))
                    self._map_what_the_checker_does_not_look_at(originals)
        if isinstance(self._type_run_baseline, CheckFailure):
            # A checker that cannot run at all -- a plugin it cannot load, a
            # tree it cannot build -- leaves the same user in the same place as
            # one reporting errors, and said nothing about how to get out of it.
            # A configuration mypy only warns about is not this: one naming a
            # Python version mypy has dropped (Voluptuous and Lark ask for 3.9
            # and 3.8) is checked with mypy's oldest, as mypy checks it.
            raise RefactoringError(
                f"Original project type check failed: {self._type_run_baseline.reason}\n"
                f"{UNTYPED_REMEDY}"
            )

    def _active_type_oracle(self) -> Optional[TypeOracle]:
        """The caller's oracle once this run's original check has completed, errors or not."""
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
        if module_level and host is not None:
            selfless = self._self_as_type_variable(proposal, host, sites)
            if selfless is not None:
                proposal.extracted_function, proposal.helper_type_declarations = selfless
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
        host = self._parsed_host(proposal.file_path)
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
            variant = dataclasses.replace(
                proposal,
                extracted_function=candidate.helper,
                required_imports=candidate.required_imports,
                type_checking_imports=candidate.type_checking_imports,
                helper_type_declarations=candidate.declarations,
            )
            # Its own imports, never the ordinary signature's: those the
            # generic rung wrote for the classes it names, and any a name it
            # left qualified needs once shortened. A type the variant
            # abstracts needs none.
            shortened = self._shorten_unreachable_names(variant, host)
            variant.type_checking_imports = tuple(
                dict.fromkeys(candidate.type_checking_imports + shortened)
            )
            yield variant

    def _self_as_type_variable(
        self, proposal: RefactoringProposal, host: ast.Module, sites: Sequence[ApplySite]
    ) -> Optional[Tuple[ast.FunctionDef, Tuple[ast.stmt, ...]]]:
        """The module-level helper with ``Self`` spelled as a bound type variable (``self_as_type_variable``).

        The bound names the classes the sites are methods of, so each must be
        a class the host module defines at its top level, where a checker
        resolves the bound's string.
        """
        classes: List[str] = []
        defined = _defined_names(host)
        for site in sites:
            method = method_at(site.source, site.start_line)
            if method is None or method.class_name not in defined:
                return None
            classes.append(method.class_name)
        reserved = {node.id for node in ast.walk(host) if isinstance(node, ast.Name)}
        reserved |= defined | _import_bound_names(host)
        reserved |= {
            node.id for node in ast.walk(proposal.extracted_function) if isinstance(node, ast.Name)
        }
        return self_as_type_variable(
            proposal.extracted_function,
            classes,
            reserved,
            [site.declared_return for site in sites],
            all(isinstance(site.statement, ast.Return) for site in sites),
        )

    def _ladder_policy(self, file_path: str) -> _LadderPolicy:
        """What the project's mypy settles about the fallback rungs for a helper in ``file_path``.

        Only mypy's options are read, and only when mypy is among the checkers
        that verify: a Pyright project keeps every rung.
        """
        if not verifies_with_mypy(self._type_run_oracle):
            return _LadderPolicy()
        origin = self._origin_of(file_path)
        known = self._ladder_policies.get(origin)
        if known is None:
            flags = mypy_ladder_flags(Path(origin))
            known = _LadderPolicy(
                annotations_required=flags["disallow_untyped_defs"],
                returning_any_refused=flags["warn_return_any"],
                untyped_bodies_checked=flags["check_untyped_defs"],
                mypy=True,
            )
            self._ladder_policies = {**self._ladder_policies, origin: known}
        return known

    def _annotation_ladder(
        self, proposal: RefactoringProposal, check_types: bool, hearing: Hearing
    ) -> Iterator[RefactoringProposal]:
        """The signatures a proposal is tried with, most precise first, each after the last is refused.

        Untyped, or reusing a function whose signature stays, the proposal as it
        is. Otherwise: generic candidates first when the ordinary signature has
        already lost information to ``Any``, then the ordinary signature, then
        generic candidates when it had not; then the targeted rung, ``Any``
        exactly where the ordinary signature's own errors point
        (``targeted_any``); then every annotation ``Any``, where the checker
        does not refuse a helper returning ``Any`` wherever its value is
        returned (mypy's ``warn_return_any``: under strict mypy the rung
        verified 4 of 46 times on the corpus, and 32 of its 42 refusals were
        exactly that); last the unannotated helper, when the latest errors all
        lie inside the helper and the project does not require annotations
        (``disallow_untyped_defs``). A helper inference left bare still gets
        the fallback rungs: bare is only the ordinary signature, not the last.

        Generation is lazy and reads ``hearing`` between rungs, so the targeted
        rung is built from the ordinary rung's errors, and a refusal the judge
        finds no signature can answer ends the ladder at once. Each rung is
        named in a ``TYPES`` debug line before it is tried.
        """
        if not check_types or proposal.reused_function is not None:
            yield proposal
            return
        known = self._untypeable_as_proposed(proposal)
        if known is not None:
            hearing.settle(known)
            return
        policy = self._ladder_policy(proposal.file_path)
        lossy = self._helper_uses_any(proposal)

        def generics() -> Iterator[RefactoringProposal]:
            for index, variant in enumerate(self._generic_helper_variants(proposal)):
                TYPES.debug("ladder rung generic#%d for %s", index, proposal.description)
                yield self._finished(variant)
                if hearing.settled_by() is not None:
                    return

        if lossy:
            yield from generics()
            if hearing.settled_by() is not None:
                return
        ordinary = self._finished(proposal)
        TYPES.debug("ladder rung ordinary for %s", proposal.description)
        yield ordinary
        if hearing.settled_by() is not None:
            return
        refusal = hearing.of(ordinary)
        if not lossy:
            yield from generics()
            if hearing.settled_by() is not None:
                return
        targeted = self._targeted_variant(ordinary, refusal) if refusal is not None else None
        if targeted is not None:
            TYPES.debug("ladder rung targeted for %s", proposal.description)
            yield targeted
            if hearing.settled_by() is not None:
                return
        if not policy.returning_any_refused and (
            self._helper_has_annotations(proposal) or proposal.wants_type_inference
        ):
            every_any = self._with_every_annotation_any(proposal)
            if targeted is None or canonical_dump(every_any.extracted_function) != canonical_dump(
                targeted.extracted_function
            ):
                TYPES.debug("ladder rung every-Any for %s", proposal.description)
                yield self._finished(every_any)
                if hearing.settled_by() is not None:
                    return
        last = hearing.last
        if (
            last is not None
            and last.confined_to_helper()
            and not policy.annotations_required
            and self._helper_has_annotations(proposal)
        ):
            # The unannotated helper follows only when it could help; see ``Rejection``.
            host = self._parsed_host(proposal.file_path)
            if host is not None and unannotated_function(host) is None:
                hearing.settle(
                    Unanswerable(
                        Untypeable.UNANNOTATED_IN_ANNOTATED_MODULE,
                        f"every function of {Path(proposal.file_path).name} is annotated,"
                        " and a check stricter than the configuration Towel reads, such as"
                        " mypy --strict, refuses the first that is not",
                    )
                )
                return
            TYPES.debug("ladder rung unannotated for %s", proposal.description)
            yield self._finished(self._without_annotations(proposal))

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
    def _finished(variant: RefactoringProposal) -> RefactoringProposal:
        """``variant`` as it is written.

        Never ``None`` as the string ``"None"`` (``without_quoted_none``), and
        no import its annotations no longer name (``used_imports``): every rung
        but the first writes ``Any`` somewhere the ordinary signature named a
        class, and the class's import stays behind with no use.
        """
        helper = without_quoted_none(variant.extracted_function)
        declarations = variant.helper_type_declarations
        required = used_imports(variant.required_imports, helper, declarations)
        checking = used_imports(variant.type_checking_imports, helper, declarations)
        if (
            canonical_dump(helper) == canonical_dump(variant.extracted_function)
            and required == variant.required_imports
            and checking == variant.type_checking_imports
        ):
            return variant
        return dataclasses.replace(
            variant,
            extracted_function=helper,
            required_imports=required,
            type_checking_imports=checking,
        )

    def _targeted_variant(
        self, ordinary: RefactoringProposal, refusal: Rejection
    ) -> Optional[RefactoringProposal]:
        """The ordinary variant with ``Any`` where its refusal points (``targeted_any``), or None."""
        helper = targeted_any(ordinary.extracted_function, refusal, self._receiver_name(ordinary))
        if helper is None:
            return None
        host = self._parsed_host(ordinary.file_path)
        helper = written_for_python(helper, host, self._oldest_python_for(ordinary.file_path, host))
        helper, declarations = drop_unbound_variables(helper, ordinary.helper_type_declarations)
        return self._finished(
            dataclasses.replace(
                ordinary,
                extracted_function=helper,
                helper_type_declarations=declarations,
                required_imports=tuple(
                    dict.fromkeys(ordinary.required_imports + typing_imports_needed(helper, host))
                ),
            )
        )

    def _untypeable_as_proposed(self, proposal: RefactoringProposal) -> Optional[Unanswerable]:
        """Why no signature can type ``proposal``, when the proposal alone shows it; else None.

        A lambda at a call that needs a test's narrowing
        (``narrowing_needed_in_thunk``), and, where mypy verifies, a
        collection whose partial type the block would have completed
        (``partial_type_passed``). Both are the proposal's own construction,
        so no check is spent learning them; the other two reasons need the
        checker's verdict (``_judge_for``).
        """
        sites = self._annotation_sites(proposal)
        verdict = narrowing_needed_in_thunk(
            proposal.extracted_function, [site.call for site in sites]
        )
        if verdict is None and self._ladder_policy(proposal.file_path).mypy:
            verdict = partial_type_passed(
                [(site.file_path, site.source, site.start_line, site.call) for site in sites],
                lambda path: self._ladder_policy(path).untyped_bodies_checked,
            )
        return verdict

    def _judge_for(self, proposal: RefactoringProposal) -> Judge:
        """What decides that a refusal of ``proposal``'s helper is one no signature can answer.

        A narrowing the call cannot carry back to its caller
        (``narrowing_the_call_cannot_carry``), one a lambda at the call lost
        (``narrowing_refused_in_thunk``), or attribute declarations that left
        their class with the block (``declarations_leave_their_class``).
        """
        receivers = self._site_receivers(proposal)

        def judge(helper: ast.FunctionDef, rejection: Rejection) -> Optional[Unanswerable]:
            reason = narrowing_the_call_cannot_carry(helper, rejection)
            if reason is None:
                reason = narrowing_refused_in_thunk(helper, rejection)
            if reason is None and receivers:
                reason = declarations_leave_their_class(helper, rejection, receivers)
            return reason

        return judge

    def _site_receivers(self, proposal: RefactoringProposal) -> Dict[str, FrozenSet[str]]:
        """For each helper parameter some site binds to its method's own receiver, those methods' classes.

        Only a helper that is not itself a method of the class: assignments
        through a method helper's receiver still declare the class's
        attributes.
        """
        helper = proposal.extracted_function
        parameters = [argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)]
        found: Dict[str, Set[str]] = {}
        for replacement in proposal.replacements:
            path = replacement.file_path or proposal.file_path
            source = self._read_source(path)
            call = call_in_statement(replacement.node, helper.name)
            if source is None or call is None:
                continue
            method = method_at(source, replacement.line_range[0])
            if method is None or method.kind != "instance" or method.receiver is None:
                continue
            if method.class_name == proposal.insert_into_class and path == proposal.file_path:
                continue
            for parameter, argument in zip(parameters, call.args):
                if isinstance(argument, ast.Name) and argument.id == method.receiver:
                    found.setdefault(parameter, set()).add(method.class_name)
        return {parameter: frozenset(classes) for parameter, classes in found.items()}

    @classmethod
    def _helper_uses_any(cls, proposal: RefactoringProposal) -> bool:
        """Whether the ordinary signature has already lost part of its type information.

        A method helper's receiver is left bare on purpose (its class fixes
        it), so it loses nothing; counted as lost, it sent every method helper
        to the generic candidates before its own precise signature.
        """
        helper = proposal.extracted_function
        receiver = cls._receiver_name(proposal)
        annotations = [
            arg.annotation
            for arg in helper.args.posonlyargs + helper.args.args
            if arg.arg != receiver
        ]
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

        What it reports is compared as every candidate's check was
        (``towel.type_baseline``): with what the run's own checks said the
        project reports now, the errors the original had and kept included.
        Those checks were warm, and a checker started from nothing need not
        agree with them even about the original: pyright's command line and its
        language server resolved trio's modules differently and disagreed about
        errors in files no change touched. So an error only the cold check
        reports is then looked for in a cold check of the original
        (:meth:`_unseen_from_the_start`), and only one neither accounts for is
        loud: that is exactly an error the run's changes brought and its warm
        checks missed.
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
        reported = self._cold_errors(oracle, sources)
        finished = {self._where_checked(path): text for path, text in sources.items()}
        unseen = self._type_known.introduced(
            reported, where=self._where_checked, texts_after=finished.get
        )
        if unseen:
            unseen = self._unseen_from_the_start(reported, unseen, sources)
        if unseen:
            details = "\n".join(f"  {error.path}: {error.message}" for error in unseen[:3])
            raise RefactoringError(
                f"The finished project reports {len(unseen)} type error(s) that the "
                f"checker did not report while the run was in progress:\n{details}\n"
                "This is a defect in Towel's verification, not in the project; please "
                "report it. Nothing was written."
            )

    @staticmethod
    def _cold_errors(oracle: TypeOracle, sources: Mapping[str, str]) -> List[TypeDiagnostic]:
        """Every configured checker's errors about ``sources``; a check that cannot run refuses."""
        reported: List[TypeDiagnostic] = []
        for result in checks_in_turn(oracle, sources):
            if isinstance(result, CheckFailure):
                # This check exists to make a wrong answer loud; a check that
                # could not run has not confirmed anything, and saying so
                # quietly would be the same silence it was built to remove.
                raise RefactoringError(
                    "The finished project could not be confirmed by a checker started from "
                    f"nothing: {result.reason}\nNothing was written."
                )
            reported.extend(result.errors)
        return reported

    def _unseen_from_the_start(
        self,
        reported: Sequence[TypeDiagnostic],
        unseen: Tuple[TypeDiagnostic, ...],
        finished: Mapping[str, str],
    ) -> Tuple[TypeDiagnostic, ...]:
        """Those of ``unseen`` that a cold check of the original does not account for either.

        ``reported`` is the cold check of the ``finished`` project, and
        ``unseen`` what of it the run's warm checks did not report. The
        original is checked the way the finished project just was, from the
        files the run started from, and compared as a change is: a file the
        run left alone must hold the error at the same line, one it changed as
        many times. What the original's cold check reports too was there before
        the run and is not its doing. When the original cannot be read or
        checked, nothing is excused.
        """
        oracle = self.type_oracle
        if oracle is None:
            return unseen
        originals: Dict[str, str] = {}
        changed: Set[str] = set()
        for path, text in finished.items():
            original = self._origin_of(path)
            source = self._read_source(original)
            if source is None:
                return unseen
            originals[original] = source
            if source != text:
                changed.add(resolved_path(original))
        from_the_start: List[TypeDiagnostic] = []
        for result in checks_in_turn(oracle, originals):
            if isinstance(result, CheckFailure):
                return unseen
            from_the_start.extend(result.errors)
        known = KnownErrors.of(
            from_the_start, texts={resolved_path(path): text for path, text in originals.items()}
        ).moving(changed)
        texts = {self._where_checked(path): text for path, text in finished.items()}
        brought = {
            id(error)
            for error in known.introduced(
                reported, where=self._where_checked, texts_after=texts.get
            )
        }
        return tuple(error for error in unseen if id(error) in brought)

    def _project_errors(
        self, modified_files: Dict[str, str], helper_name: str
    ) -> Tuple[TypeDiagnostic, ...]:
        """What the project check says of a variant, without asking again when nothing it read changed.

        A declined proposal is heard again at a rehearing, once something has
        been applied since, and each of its rungs is rendered and checked
        again. Rendered again it is often the same text, but for the number in
        its helper's generated name (packaging: 28 of 68 checks repeated an
        earlier one exactly, errors and all). A refusal is kept with the files
        it could have depended on: those its errors lie in and every project
        file they import, followed through their imports, the variant's own
        files aside, which the key already holds. When a variant renders to a
        kept key and none of those files has changed, the check would answer
        as it did, and its answer is replayed under the helper's new name.

        Only refusals are kept; an accepted variant is applied and changes the
        project. The run forgets them all when it begins, and a refusal whose
        dependencies cannot be followed (an error in no file, too many files)
        is not kept at all.
        """
        key = _variant_key(modified_files, helper_name)
        known = self._refused_checks.get(key)
        if (
            known is not None
            and self._dependencies_unchanged(known)
            and self._known_errors_in(known.depends_on) == known.known
        ):
            self._checker_refusals += 1
            renamed = re.compile(rf"(?<!\w){re.escape(known.helper_name)}(?!\w)")
            TYPES.debug(
                "replaying the refusal of an identical variant: %d error(s)", len(known.errors)
            )
            return tuple(
                dataclasses.replace(error, message=renamed.sub(helper_name, error.message))
                for error in known.errors
            )
        errors = self._new_type_errors(modified_files)
        if errors:
            depends_on = self._refusal_dependencies(modified_files, errors)
            if depends_on is not None:
                kept = dict(self._refused_checks)
                kept[key] = _RefusedCheck(
                    errors, helper_name, depends_on, self._known_errors_in(depends_on)
                )
                while len(kept) > _REFUSED_CHECKS_KEPT:
                    kept.pop(next(iter(kept)))
                self._refused_checks = kept
        return errors

    @staticmethod
    def _refusal_dependencies(
        modified_files: Mapping[str, str], errors: Sequence[TypeDiagnostic]
    ) -> Optional[Tuple[Tuple[str, str], ...]]:
        """(path, digest) of the files a refusal could depend on, outside the variant; None if unknown."""
        variant = {os.path.realpath(path) for path in modified_files}
        pending: List[Path] = []
        for error in errors:
            path = Path(os.path.realpath(error.path))
            if not path.is_file():
                return None
            pending.append(path)
        roots = sorted(
            {
                (packages[-1].parent if packages else Path(path).parent)
                for path in [*modified_files, *(error.path for error in errors)]
                for packages in [package_chain(Path(path))]
            }
        )
        seen: Set[Path] = set()
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            if len(seen) > _DEPENDENCIES_FOLLOWED:
                return None
            text = modified_files.get(str(path))
            if text is None:
                text = next(
                    (t for p, t in modified_files.items() if os.path.realpath(p) == str(path)),
                    None,
                )
            if text is None:
                text = try_read_source(str(path))
            if text is None:
                return None
            try:
                tree = ast.parse(text)
            except SyntaxError:
                return None
            pending.extend(_imported_files(path, tree, roots) - seen)
        # The variant's own files count as they stand before the change: the
        # key holds what the change makes of them, and what the run counts as
        # their existing errors is read against this text.
        seen |= {Path(path) for path in variant}
        depends_on: List[Tuple[str, str]] = []
        for path in sorted(seen):
            text = try_read_source(str(path))
            if text is None:
                return None
            depends_on.append((str(path), _digest(text)))
        return tuple(depends_on)

    def _known_errors_in(self, depends_on: Sequence[Tuple[str, str]]) -> str:
        """A digest of the errors the run counts as the project's own in these files, and which moved.

        A change is refused only for an error the project did not already
        report (``towel.type_baseline``), and what it already reports is
        brought up to date after every applied change. A refusal replayed must
        have been judged against the same account of these files.
        """
        places = {self._where_checked(path) for path, _ in depends_on}
        known = self._type_known
        errors = sorted(
            (error.path, error.line or 0, error.message)
            for error in known.errors
            if error.path in places
        )
        return _digest(repr((errors, sorted(known.moved & places))))

    @staticmethod
    def _dependencies_unchanged(known: _RefusedCheck) -> bool:
        for path, digest in known.depends_on:
            text = try_read_source(path)
            if text is None or _digest(text) != digest:
                return False
        return True

    def _new_type_errors(self, modified_files: Dict[str, str]) -> Tuple[TypeDiagnostic, ...]:
        """What the project, unchanged consumers included, would report with the change and not now.

        The project as it stands is checked with ``modified_files`` over it,
        and what that check reports is compared with what the project reports
        already (``towel.type_baseline``): an error the original project had,
        and still has, is not the change's. Every configured checker must
        accept, so the first to report a new error settles it and the rest are
        not asked (:func:`~towel.type_inference.checks_in_turn`). An accepted
        change's check is kept: once the driver has written it, it is what the
        project reports, and the next change is compared with it
        (:meth:`_follow_the_written_change`).
        """
        oracle = self._active_type_oracle()
        if oracle is None:
            raise RefactoringError("Type checking was requested without a type oracle")
        checked = {self._where_checked(path): text for path, text in modified_files.items()}
        changing = frozenset(checked)

        def seen(path: str) -> Optional[str]:
            """The text of ``path`` (resolved, the original's) that this check sees."""
            text = checked.get(path)
            return text if text is not None else self._read_source(self._as_checked(path))

        reported: List[TypeDiagnostic] = []
        introduced: Tuple[TypeDiagnostic, ...] = ()
        for result in checks_in_turn(oracle, modified_files):
            if isinstance(result, CheckFailure):
                raise CheckerUnavailableError(
                    f"Prospective project type check failed: {result.reason}"
                )
            reported.extend(result.errors)
            introduced = self._type_known.introduced(
                reported, changing, where=self._where_checked, texts_after=seen
            )
            if introduced:
                break
        for diagnostic, count in Counter(introduced).items():
            TYPES.debug("new error x%d in %s: %s", count, diagnostic.path, diagnostic.message)
        if introduced:
            # Counted so a proposal no variant of which survives is reported as
            # the checker's refusal, not as something that could not be rendered.
            self._checker_refusals += 1
            return introduced
        self._refuse_what_the_checker_does_not_look_at(oracle, modified_files)
        # What this check saw of each file: the change's texts, and those of
        # files an earlier change may have altered as they now stand; the rest
        # have kept the text the reference records.
        texts = {**self._type_known.texts, **checked}
        for path in self._type_known.moved - changing:
            text = seen(path)
            if text is None:
                texts.pop(path, None)
            else:
                texts[path] = text
        self._type_known = self._type_known.moving(changing)
        self._type_checked = CheckedChange(
            tuple(modified_files.items()),
            KnownErrors.of(reported, self._where_checked, texts=texts),
        )
        return ()

    def _follow_the_written_change(self) -> None:
        """The driver wrote the change the checker last accepted; compare the next with its check.

        That check was made of the project as it stood with the change over
        it, which is the project as it now stands, so what it reported is what
        the project reports: every line in it is where it is now. Comparing the
        next change with the original's errors instead would let one change
        spend what another removed: an error a first change made disappear, and
        a second brought back with the same message in the same file, would
        count against the original's and pass. When what was checked is not
        what the files hold, nothing is assumed, and the files it named stay
        among those whose lines may have moved.
        """
        checked, self._type_checked = self._type_checked, None
        if checked is None:
            return
        if all(self._read_source(path) == text for path, text in checked.files):
            self._type_known = checked.reported

    def _where_checked(self, path: str) -> str:
        """``path`` in the project whose check the run compares with: the original, resolved."""
        return resolved_path(self._origin_of(path))

    def _as_checked(self, original: str) -> str:
        """``original`` (resolved) where the run checks it: in its private copy, when it has one.

        The inverse of :meth:`_where_checked`, for reading what a check saw of
        a file the change did not supply.
        """
        origin = self._output_origin
        if origin is None:
            return original
        source, destination = (root.resolve() for root in origin)
        absolute = Path(original)
        if absolute == source:
            return str(destination)
        if absolute.is_relative_to(source):
            return str(destination / absolute.relative_to(source))
        return original

    def _decline_what_the_checker_cannot_see(self, paths: Sequence[str]) -> None:
        """Refuse a change to a file where the original check names what it cannot type.

        An import the checker cannot resolve or finds no types for, or a
        decorator without types, leaves a name that is ``Any`` wherever it
        goes. ``Any`` accepts every use, so no check can reject a change that
        misuses what such a name carries, and the subtype questions that
        normalize a helper's annotations answer yes about it whatever the
        project's own check, which may see its real type, would say. All 17
        corpus projects that type-check pass their own check as their CI runs
        it, so each such error Towel's check reports in one of them is one
        their check does not: there Towel's is either blind where theirs is
        not, or looking at a file theirs leaves alone, and neither verdict is
        worth accepting a change for. A file that only imports from such a
        module is not declined; see docs/KNOWN_LIMITATIONS.md.
        """
        if self._type_run_oracle is None or not self._type_names_any:
            return
        for path in dict.fromkeys(paths):
            errors = self._type_names_any.get(self._where_checked(path))
            if errors:
                raise UnverifiableChangeError(
                    f"{path} holds {len(errors)} name(s) the type checker cannot type, "
                    f"so a change there cannot be verified: {errors[0].message}"
                )

    def _report_pre_existing(self, analyzed: Sequence[str]) -> None:
        """Say, before anything else, what the original check reports and what it cannot see.

        The errors are left as they are, so a user learns what verification
        will compare with. Those that leave a name the checker cannot type are
        named with their files, since no change to those files is attempted:
        installing what they import, with its stubs, where Towel runs is what
        has them verified.
        """
        errors = self._type_known.errors
        if not errors:
            return
        root = find_project_root(Path(analyzed[0])) if analyzed else Path.cwd()
        LOG.warning(pre_existing_summary(errors, root))
        changed = frozenset(self._where_checked(path) for path in analyzed)
        warning = names_any_warning(self._type_names_any, root, changed)
        if warning:
            LOG.warning(warning)

    def _unlooked(
        self,
        oracle: TypeOracle,
        probed: Mapping[str, str],
        wanted: Mapping[str, Sequence[Place]],
        *,
        every_checker: bool,
    ) -> Dict[str, Set[Place]]:
        """For each file of ``probed``, the ``wanted`` statements a checker does not look at.

        A probe goes before each statement (``towel.reachability``), and a
        statement the checker gives no answer for is one it takes to be
        unreachable. ``every_checker`` asks each checker behind ``oracle``, as
        verification must; otherwise the one that infers, which is enough to
        name regions before a run. A module no probe can be placed in, or a
        checker that answers nothing a mapping can hold, is taken to look at
        none of it.
        """
        requests: List[RevealRequest] = []
        unseen: Dict[str, Set[Place]] = {}
        planned = {}
        for path, text in probed.items():
            plan = probe_plan(text)
            if plan is None:
                unseen[path] = set(wanted[path])
                continue
            planned[path] = plan
            for line, indent in sorted({plan.sites[p] for p in wanted[path] if p in plan.sites}):
                requests.append(RevealRequest(path, plan.text, line, indent, (PROBE,)))
        answers: Tuple[Mapping[RevealKey, str], ...] = ()
        if requests:
            answers = (
                reveal_by_each(oracle, requests) if every_checker else (oracle.reveal(requests),)
            )
        for path, plan in planned.items():
            unseen[path] = set()
            for place in wanted[path]:
                site = plan.sites.get(place)
                if site is None:
                    continue  # an ``elif``: its body answers for it
                key = (path, site[0], 0)
                if not all(isinstance(answer, Mapping) and key in answer for answer in answers):
                    unseen[path].add(place)
        return unseen

    def _map_what_the_checker_does_not_look_at(self, originals: Mapping[str, str]) -> None:
        """Find, before a run, the regions of the analyzed files the checker does not look at.

        A block (a body, an ``else``, a handler, the statements after an
        ``assert``) whose first statement the checker does not answer a probe
        at is one it takes to be unreachable, and so are the blocks inside it.
        They are named now, since no change to them will be attempted
        (:meth:`_decline_what_the_checker_does_not_look_at`), and kept with the
        text they were found in, for as long as a file still holds it. The
        checker that infers is asked, in one probe build; every change is still
        asked of every checker before it is accepted
        (:meth:`_refuse_what_the_checker_does_not_look_at`).
        """
        oracle = self._type_run_oracle
        if oracle is None or not originals:
            return
        wanted: Dict[str, List[Place]] = {}
        blocks: Dict[str, Dict[Place, Tuple[int, int]]] = {}
        whole: Dict[str, Tuple[int, int]] = {}
        for path, text in originals.items():
            plan = probe_plan(text)
            if plan is None:
                whole[path] = (1, max(1, len(text.split("\n"))))
                continue
            blocks[path] = {place: (start, end) for place, start, end in plan.blocks}
            wanted[path] = list(blocks[path])
        unseen = self._unlooked(
            oracle, {path: originals[path] for path in wanted}, wanted, every_checker=False
        )
        regions: Dict[str, Tuple[Tuple[int, int], ...]] = {}
        for path, places in unseen.items():
            spans = sorted(blocks[path][place] for place in places)
            outermost: List[Tuple[int, int]] = []
            for start, end in spans:
                if outermost and start <= outermost[-1][1]:
                    continue  # inside the region before it
                outermost.append((start, end))
            if outermost:
                regions[path] = tuple(outermost)
        for path, span in whole.items():
            regions[path] = (span,)
        self._type_unlooked = {
            self._where_checked(path): (_digest(originals[path]), spans)
            for path, spans in regions.items()
        }
        if regions:
            root = find_project_root(Path(next(iter(originals))))
            LOG.warning(
                unlooked_warning(
                    {self._where_checked(path): spans for path, spans in regions.items()}, root
                )
            )

    def _decline_what_the_checker_does_not_look_at(self, proposal: RefactoringProposal) -> None:
        """Refuse, before anything is inferred, a proposal that replaces code the checker skips.

        Only while the file still holds the text the regions were found in;
        once a change has moved its lines, every change is still asked of the
        checker itself before it is accepted.
        """
        if not self._type_unlooked:
            return
        for replacement in proposal.replacements:
            path = replacement.file_path or proposal.file_path
            found = self._type_unlooked.get(self._where_checked(path))
            text = self._read_source(path) if found is not None else None
            if found is None or text is None or _digest(text) != found[0]:
                continue
            first, last = replacement.line_range
            for start, end in found[1]:
                if start <= last and first <= end:
                    raise UncheckedCodeError(
                        f"{path}:{start}-{end} is code the type checker does not look at "
                        "(it takes it to be unreachable on the platform and Python it checks "
                        "for), so a change there cannot be verified"
                    )

    def _refuse_what_the_checker_does_not_look_at(
        self, oracle: TypeOracle, modified_files: Mapping[str, str]
    ) -> None:
        """Refuse a change that writes a line some checker does not look at.

        Asked of a change every checker has accepted, before it counts as
        verified: the lines it wrote -- the call sites, the helper, its
        imports -- found by aligning each file with what it held
        (``towel.type_baseline.unchanged_lines``), and each statement that
        begins on one is probed, by every checker. A checker reports nothing
        in code it takes to be unreachable, so its acceptance there said
        nothing: trio's CI checks linux, darwin and win32, and a helper
        accepted where a module asserts another platform failed there.
        """
        wanted: Dict[str, List[Place]] = {}
        for path, text in modified_files.items():
            plan = probe_plan(text)
            if plan is None:
                raise UncheckedCodeError(
                    f"{path}: no probe can be placed in the changed module, so what the type "
                    "checker looks at in it is not known and the change cannot be verified"
                )
            before = self._read_source(path)
            kept = unchanged_lines(before, text).unchanged_after if before is not None else set()
            wanted[path] = [place for place, _ in plan.spans if place[0] not in kept]
        unseen = self._unlooked(oracle, modified_files, wanted, every_checker=True)
        for path, places in unseen.items():
            if places:
                line = min(places)[0]
                raise UncheckedCodeError(
                    f"{path}:{line}: the type checker does not look at the code this change "
                    "writes there (it takes it to be unreachable on the platform and Python it "
                    "checks for), so the change cannot be verified"
                )
