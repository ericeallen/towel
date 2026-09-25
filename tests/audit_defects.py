"""The defects a release audit reported that the suite still expects to fail, as one line each.

Each constant here is the reason of a strict expected failure
(:func:`tests.hostile_refactoring.with_known_defects`): the batteries' fixtures
and the differential seeds that expose a defect name it in their
``KNOWN_DEFECTS``, so the fix of one defect flips every test that shows it,
and the reason says which. Write one as ``"<audit id>: <the defect, in a
line>"``, and remove it once nothing names it.

None is open. The round-3 audit's eight P1s (Towel 1.772 at 077a712,
dimension 2, adversarial semantic equivalence) are all fixed, and every
fixture and seed ported for them passes.
"""
