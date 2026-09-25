"""The defects a release audit reported that the suite still expects to fail, as one line each.

Each is the reason of a strict expected failure
(:func:`tests.hostile_refactoring.with_known_defects`): the batteries' fixtures
and the differential seeds that expose it name it here, so the fix of one
defect flips every test that shows it, and the reason says which. Ids are the
round-3 audit's (Towel 1.772 at 077a712, dimension 2, adversarial semantic
equivalence). Remove a constant once nothing names it.
"""

from __future__ import annotations

P1_1_PREBOUND_REBINDING = (
    "P1-1: a for target, match capture or nested def rebinding a name bound before the block"
    " is taken as a fresh binding, so the helper reads it unbound"
)
P1_2_SEMICOLON_LINE = (
    "P1-2: a block that starts or ends inside a ';' line is spliced by whole lines,"
    " deleting the line's other statement"
)
P1_3_LEADING_THUNK = (
    "P1-3: the leading-thunk pass passes a thunk eagerly although an effect or an"
    " exception precedes it in the helper"
)
P1_4_ANNOTATION_IMPORT = (
    "P1-4: a typing import added for a helper's annotation binds or rebinds a public name"
    " of the module, TYPE_CHECKING among them"
)
P1_5_RENAMED_BINDER = (
    "P1-5: binders renamed between the sites share one spelling in the helper, which leaks"
    " into the UnboundLocalError message"
)
P1_6_TOP_LEVEL_INSIDE_PACKAGE = (
    "P1-6: --cross-module neither refuses nor sets aside a module file inside the package"
    " that is imported top-level"
)
P1_7_READ_BEFORE_BIND = (
    "P1-7: a name the block reads before binding it is passed in from the call site, where"
    " it is no longer local"
)
P1_8_LATER_UPDATE = (
    "P1-8: a later += or del of a name the block binds is not counted as a read of it,"
    " so the helper does not return it"
)
