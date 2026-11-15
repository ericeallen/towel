# Copyright 2026 Eric Allen
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

"""Why Towel left code alone, in one plain sentence per reason, and what the user can do.

A run counts what it did not extract under two vocabularies: a candidate
pair the analysis declined, under a ``RejectReason``, and a proposal it built
and then did not apply, under a ``DeclineReason`` (which includes the
``Untypeable`` reasons). Every member of each has an entry here, and a test
holds the table to that, so a new reason cannot ship without its sentence.

A sentence says what was true of the code, not what a check is called; the
remedy, when there is one, says what the user can change. Most reasons have
none: the code is left alone because changing it could change what the
program does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, get_args

from towel.unification.exceptions import Untypeable
from towel.unification.models import RejectReason


@dataclass(frozen=True)
class Explanation:
    """Why some code was left alone, and what the user can do about it, if anything."""

    sentence: str
    remedy: Optional[str] = None


def _says(sentence: str, remedy: Optional[str] = None) -> Explanation:
    return Explanation(sentence, remedy)


_CROSS_MODULE = "Only with --cross-module: "

_UNPROFITABLE = _says(
    "The shared code does not repay the helper: the helper and the calls that replace the"
    " blocks would not be smaller than the blocks themselves (counted in syntax, not lines)."
)

PAIR_REASONS: Mapping[RejectReason, Explanation] = {
    RejectReason.BARE_NAME_DIFFERS_BY_MODULE: _says(
        _CROSS_MODULE + "a helper shared between modules would read a module-level name that"
        " means something different in each of them."
    ),
    RejectReason.BUILTIN_ARGUMENT: _says(
        "The call would have to pass a builtin such as len to the helper, or one block uses a"
        " variable named like a builtin that the other uses as the builtin.",
        "Rename the variable that shadows the builtin.",
    ),
    RejectReason.BUILTIN_MAPPING_REPLACED: _says(
        "Code in the project assigns or deletes __builtins__, so code moved into a helper could"
        " see different builtins.",
        "Remove that code, or leave its directory out with --exclude.",
    ),
    RejectReason.BUILTIN_MAY_DIFFER_BY_MODULE: _says(
        _CROSS_MODULE + "the shared helper would look up a builtin such as len in the module"
        " holding the helper, not in the module the code came from.",
        "Pass --parameterize-builtins to hand such builtins to the helper as arguments.",
    ),
    RejectReason.CLOSURE_CROSSES_BLOCK_BOUNDARY: _says(
        "A nested function, lambda or comprehension and the block use a variable of the same"
        " name that one of them reassigns, so moving the block could change which variable"
        " the closure sees.",
        "Rename the lambda parameter or comprehension variable if it only shares the name.",
    ),
    RejectReason.CONDITIONALLY_BOUND_RETURN: _says(
        "A variable the helper would hand back is not assigned on every path through the"
        " block, for example only inside a loop or an if.",
        "Assign it on every path inside the block.",
    ),
    RejectReason.CONDITIONALLY_IMPORTED_HOST: _says(
        _CROSS_MODULE + "the project imports the module that would hold the helper only"
        " under a condition."
    ),
    RejectReason.CREATED_OBJECT_ESCAPES: _says(
        "The block creates a function, lambda, class or generator that is used after the block,"
        " or handed to code Towel cannot see (such as items.sort(key=lambda ...)).",
        "Use sorted, min or max with key=, or define the function at module level.",
    ),
    RejectReason.OWNED_BINDING_EXCEPTION_BOUNDARY: _says(
        "The block is inside a try or with statement and assigns a variable; if the helper"
        " raised, that variable's object would be released earlier than it is now."
    ),
    RejectReason.OWNERSHIP_PARAMETER_BUDGET: _says(
        "Keeping the function's arguments alive exactly as long as now needs one more hidden"
        " parameter, which would take the helper past --max-parameters.",
        "Raise --max-parameters.",
    ),
    RejectReason.CROSS_MODULE_GLOBAL_DECLARATION: _says(
        _CROSS_MODULE + "one of the modules involved has a function that declares a name" " global."
    ),
    RejectReason.EXISTING_HELPER_BECOMES_FORWARDER: _says(
        "The extraction would turn a helper made earlier in this run into a one-line call of"
        " another helper."
    ),
    RejectReason.FRAME_READ_IN_FUNCTION: _says(
        "The block reads super or __class__, which gives its function the class cell that a"
        " call of super through another name, elsewhere in the function, needs."
    ),
    RejectReason.FRAME_SENSITIVE_BLOCK: _says(
        "The block only works in its own function: it holds yield, await, async for or async"
        " with, a break or continue for a loop outside it, an assignment expression in a"
        " comprehension, or a super() call that needs its own method."
    ),
    RejectReason.HOST_HAS_STUB: _says(
        _CROSS_MODULE + "the module that would hold the helper has a .pyi stub, which type"
        " checkers read in its place and which would not list the helper."
    ),
    RejectReason.IMPORT_CYCLE: _says(
        _CROSS_MODULE + "whichever module held the helper, the new import would be circular."
    ),
    RejectReason.IMPORT_TIME_EFFECTS: _says(
        _CROSS_MODULE + "the new import could run module-level code, load a module the"
        " package had not loaded, or change the order modules load in.",
        "Sharing works when the module that would borrow the helper already imports the module"
        " holding it, unconditionally, at its top.",
    ),
    RejectReason.IMPURE_EAGER_PARAMETER: _says(
        "An expression that differs between the blocks would be evaluated earlier, at the call,"
        " and it is neither a constant nor a plain variable."
    ),
    RejectReason.INCOMPLETE_LIFETIME_BLOCK1: _says(
        "The first block reads a variable that is assigned only later in its function, or"
        " before the block's own assignment to it."
    ),
    RejectReason.INCOMPLETE_LIFETIME_BLOCK2: _says(
        "The second block reads a variable that is assigned only later in its function, or"
        " before the block's own assignment to it."
    ),
    RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK1: _says(
        "The first block returns on some paths and carries on on others; early-return guards"
        " are not extracted."
    ),
    RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK2: _says(
        "The second block returns on some paths and carries on on others; early-return guards"
        " are not extracted."
    ),
    RejectReason.INSTANTIATION_MISMATCH: _says(
        "Towel's own check failed: putting a call's arguments back into the helper did not give"
        " back the original code."
    ),
    RejectReason.MIXED_RETURN_AND_VARIABLES: _says(
        "The block both returns from its function and assigns variables that later code reads."
    ),
    RejectReason.MODULE_DATA_LOOKUP: _says(
        "The block reads a module-level variable (data, not a function, class or import) that"
        " would have to be passed as an argument, fixing its value at the call."
    ),
    RejectReason.MOVES_ONLY_BINDING: _says(
        "The block holds its function's only assignment to a name that other code in the"
        " function reads, and the call would not assign it."
    ),
    RejectReason.MOVES_SCOPE_DECLARATION: _says(
        "The block holds a global or nonlocal statement that code outside the block depends on."
    ),
    RejectReason.NARROWING_LOST_AT_CALL_SITE: _says(
        "The block holds a type test (isinstance, is None) that code left behind, or an"
        " argument, relies on; inside a helper the type checker would not see it."
    ),
    RejectReason.NEEDS_CLASS_BODY: _says(
        "The block calls super() without arguments, and the helper cannot be a method of that"
        " class."
    ),
    RejectReason.NESTED_BINDING_ESCAPES: _says(
        "The block is inside a loop or branch and assigns a variable that code elsewhere in the"
        " function reads."
    ),
    RejectReason.NEW_IMPORT_REQUIREMENT: _says(
        _CROSS_MODULE + "importing the module holding the helper would make the other module"
        " need a package it does not need today."
    ),
    RejectReason.NEW_TOP_LEVEL_PACKAGE: _says(
        _CROSS_MODULE + "the helper would live in a top-level package the other module never"
        " imports."
    ),
    RejectReason.NONLOCAL_SAFETY_SKIP: _says(
        "The block reads, assigns or declares a nonlocal variable."
    ),
    RejectReason.NOT_STRUCTURALLY_SIMILAR: _says(
        "The blocks have the same kinds of statements but are less than 60% alike in detail."
    ),
    RejectReason.ORPHANED_VARIABLES: _says(
        "Later code would read a variable that only the block assigns and the helper does not"
        " hand back."
    ),
    RejectReason.OTHER_DISTRIBUTION: _says(
        _CROSS_MODULE + "the two modules ship in different installable distributions."
    ),
    RejectReason.PRIVATE_NAME_LEXICAL_CLASS: _says(
        "The block uses a double-underscore private name (self.__x), and the helper would live"
        " outside that class."
    ),
    RejectReason.REBOUND_EXTERNAL_BINDING: _says(
        "The block reads a variable that another function can reassign (through global,"
        " nonlocal or a closure) while the block runs."
    ),
    RejectReason.OWNED_BINDING_FRAME_BOUNDARY: _says(
        "Moving the block would change the order in which its function releases objects when it"
        " returns: the function's arguments or other variables on one side, variables the block"
        " assigns on the other."
    ),
    RejectReason.RELATIVE_IMPORT_ACROSS_PACKAGES: _says(
        _CROSS_MODULE + "the block holds a relative import that would name a different module"
        " from the module holding the helper.",
        "Use an absolute import.",
    ),
    RejectReason.RETURN_VARIABLES_NOT_ALIGNED: _says(
        "The variables each block would hand back do not correspond one to one, or handing them"
        " back would change the order their objects are released in."
    ),
    RejectReason.RETURN_VERSUS_VARIABLES: _says(
        "One block ends by returning; the other is followed by code that reads its variables."
    ),
    RejectReason.RUN_BY_PATH_IMPORT: _says(
        _CROSS_MODULE + "one module is run as a script, where the new import would not resolve."
    ),
    RejectReason.SCOPE_DECLARATIONS_DIFFER: _says(
        "One function declares a name global or nonlocal and the other does not."
    ),
    RejectReason.SUPER_IN_CALL: _says(
        "The call replacing the block would have to evaluate super() inside a lambda, where it"
        " does not work."
    ),
    RejectReason.TRIVIAL_FORWARDING_HELPER: _says(
        "The helper would hold only a single call, raise or rename, and no logic of its own."
    ),
    RejectReason.TRIVIAL_RETURN_BLOCKS: _says(
        "The helper would compute nothing: it would return what it was given."
    ),
    RejectReason.UNBINDS_EXTERNAL_NAME: _says(
        "The block deletes (with del or except ... as) a variable assigned before it."
    ),
    RejectReason.DUPLICATE_PROPOSAL: _says(
        "Another pair of these blocks already proposed the same extraction; nothing was lost."
    ),
    RejectReason.DIRECTIVES_DIFFER: _says(
        "The blocks carry different tool comments (# type: ignore, # noqa, # pragma: no cover)"
        " on matching lines.",
        "Make the comments the same.",
    ),
    RejectReason.DIRECTIVE_ON_ARGUMENT: _says(
        "A tool comment covers an expression that would move to the call as an argument, where"
        " the comment would no longer cover it.",
        "Remove the comment, or make the lines identical.",
    ),
    RejectReason.DIRECTIVE_OUTLIVES_BLOCK: _says(
        "A region or file-wide tool comment that starts in the block also covers code after it.",
        "Close the region inside the block.",
    ),
    RejectReason.DIRECTIVE_AROUND_BLOCK: _says(
        "A tool comment outside the block (the line above, the enclosing def or if line, an"
        " open # fmt: off) covers the block and would not cover the helper.",
        "Remove or move the comment.",
    ),
    RejectReason.EXCLUDED_BLOCK_START: _says(
        "The block starts with a statement excluded from coverage; the call replacing it would"
        " be measured and never run."
    ),
    RejectReason.DIRECTIVE_ON_SHARED_LINE: _says(
        "The block shares a line that carries a tool comment with code that stays"
        " (a = 1; b = 2  # noqa).",
        "Put the statements on separate lines.",
    ),
    RejectReason.LAYOUT_NOT_KEPT: _says(
        "A # fmt: skip or # fmt: off comment requires lines of the block to stay exactly as"
        " written, and the helper cannot keep them.",
        "Remove the formatter comment.",
    ),
    RejectReason.FORWARDED_CALLEE: _says(
        "The blocks call different things that are not plain names, such as"
        " client.get_profile(...) and client.get_preferences(...).",
        "Assign the method to a variable first, then call the variable.",
    ),
    RejectReason.THUNK_OF_POSSIBLY_UNBOUND_LOCAL: _says(
        "An argument would be wrapped in a lambda and reads a local variable that may have no"
        " value yet; the error raised then would change from UnboundLocalError to NameError.",
        "Assign the variable on every path before the block.",
    ),
    RejectReason.UNDEFINED_NAMES_IN_CALL: _says(
        "The call replacing the block would use a name that does not exist where the call"
        " stands."
    ),
    RejectReason.LITERAL_POOL_BOUNDARY: _says(
        "The block holds constants whose identity (what `is` compares) could change if the code"
        " were compiled in another function or module, such as arithmetic on float literals or"
        " a one-byte bytes literal."
    ),
    RejectReason.UNIFICATION_FAILED: _says(
        "The blocks differ in something a parameter cannot stand for: an operator, text inside"
        " an f-string, a variable named differently inside a loop or branch, a method name, a"
        " slice, or more differences than --max-parameters allows.",
        "Raise --max-parameters, use the same local names, or build the string with + or"
        " format.",
    ),
    RejectReason.UNPROFITABLE_HELPER: _UNPROFITABLE,
    RejectReason.UNPROVEN_IMPORT: _says(
        _CROSS_MODULE + "no existing import in the project shows how one of the modules can"
        " import the other.",
        "See 'Resolving import refusals' in docs/CROSS_MODULE.md.",
    ),
    RejectReason.UNSUPPORTED_EXTRACTION: _says(
        "Towel could not write the helper or one of its calls."
    ),
    RejectReason.UNSAFE_REASSIGNMENT_BLOCK1: _says(
        "The first block reassigns a variable first assigned before it, such as total += x with"
        " total = 0 above."
    ),
    RejectReason.UNSAFE_REASSIGNMENT_BLOCK2: _says(
        "The second block reassigns a variable first assigned before it, such as total += x"
        " with total = 0 above."
    ),
    RejectReason.VALUE_PRODUCING_MISMATCH: _says(
        "One block produces a value (it returns, or later code reads its variables) and the"
        " other does not."
    ),
}
"""Why a candidate pair was declined, for every ``RejectReason``."""

OTHER_PAIR_REASON = _says("The pair was declined without a recorded reason.")
"""A pair declined without a traced reason, which the run counts as ``other``."""

PROPOSAL_REASONS: Mapping[str, Explanation] = {
    "refused by the type checker": _says(
        "The project's type checker reported a new error with the helper in place."
    ),
    "not judged: the type checker could not run": _says(
        "The type checker could not run for this change, so it was not judged (see the"
        " warnings above)."
    ),
    "not verifiable: its file holds a name the type checker cannot type": _says(
        "Its file uses a name the type checker cannot type, such as an import it cannot"
        " resolve, so a change there could not be checked.",
        "Install the package or its stubs where the type checker can find them.",
    ),
    "not verifiable: the type checker does not look at the code it changes": _says(
        "The type checker takes this code to be unreachable on the platform and Python version"
        " it checks for, so a change to it could not be checked."
    ),
    "not representable in its file's encoding": _says(
        "The changed file would hold a character its declared encoding cannot represent."
    ),
    "not writable in place: its file is hard-linked": _says(
        "Its file has more than one hard link, and an in-place run would leave the other links"
        " holding the old text.",
        "Write the result to a new output, or give the file a single link.",
    ),
    "the formatter changed its code or failed": _says(
        "The project's formatter failed on the changed code, or changed more than its layout.",
        "Pass --no-format to leave the generated code unformatted.",
    ),
    "the configured Ruff check failed or introduced diagnostics": _says(
        "The project's Ruff check failed on the changed file, or reported something new there."
    ),
    "could not be rendered": _says("Towel could not write the helper or one of its calls."),
    "unprofitable helper": _UNPROFITABLE,
    "changed nothing": _says("Applying it would have left the code exactly as it was."),
}
"""Why a proposal was built and then not applied, for every string ``DeclineReason``."""

UNTYPEABLE_REASONS: Mapping[Untypeable, Explanation] = {
    Untypeable.NARROWING_READ_AFTER_CALL: _says(
        "The block tests a type (isinstance, is None) that code after it relies on, and the type"
        " checker would not carry that test out of a helper."
    ),
    Untypeable.NARROWING_READ_IN_THUNK: _says(
        "A type test that one argument relies on would be outside the lambda passing it, where"
        " the type checker cannot see it."
    ),
    Untypeable.ATTRIBUTE_DECLARATIONS: _says(
        "The block's assignments through self declare its class's attributes, and a helper"
        " outside the class would declare none."
    ),
    Untypeable.PARTIAL_TYPE: _says(
        "The type checker learns an empty collection's element type from the code that would"
        " move into the helper."
    ),
    Untypeable.UNANNOTATED_IN_ANNOTATED_MODULE: _says(
        "No annotated signature of the helper type-checks, and an unannotated one would be the"
        " only unannotated function of a fully annotated module."
    ),
}
"""Why no signature of a helper can type an extraction, for every ``Untypeable``."""


def string_decline_reasons() -> frozenset[str]:
    """Every string a ``DeclineReason`` may be, apart from the ``Untypeable`` members."""
    from towel.unification.fixed_point import DeclineReason

    literal = next(arm for arm in get_args(DeclineReason) if arm is not Untypeable)
    return frozenset(get_args(literal))


def _by_value() -> Dict[str, Explanation]:
    table: Dict[str, Explanation] = {str(reason): text for reason, text in PAIR_REASONS.items()}
    table.update(PROPOSAL_REASONS)
    table.update({str(reason): text for reason, text in UNTYPEABLE_REASONS.items()})
    table["other"] = OTHER_PAIR_REASON
    return table


_BY_VALUE = _by_value()


def reason_code(counted: str) -> str:
    """The reason a count is kept under, without what it names: ``builtin_argument[len]`` -> ``builtin_argument``."""
    return counted.split("[", 1)[0]


def explain(counted: str) -> Explanation:
    """The sentence for a reason as a run counts it, with what it names, if anything, after it.

    An unknown reason is shown as it is, so a reason added without an entry
    is still reported rather than lost; the table's test refuses such a
    reason before release.
    """
    code = reason_code(counted)
    found = _BY_VALUE.get(code)
    if found is None:
        return Explanation(f"Left alone: {counted}.")
    subject = counted[len(code) :]
    if subject.startswith("[") and subject.endswith("]"):
        return Explanation(f"{found.sentence} (Here: {subject[1:-1]}.)", found.remedy)
    return found
