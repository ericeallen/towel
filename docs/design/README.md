# Design

[Documentation index](../README.md)

These pages are for contributors: how Towel is built and why it behaves as it
does. You do not need them to use Towel; the [user guides](../README.md) say
what each behavior means for your code.

- [Architecture](ARCHITECTURE.md): how the algorithms and the implementation are organized.
- [Design decisions](DECISIONS.md): the semantics and policies Towel keeps, and the reasons for each.
- [Correctness model](CORRECTNESS_MODEL.md): what Towel verifies about a transformation, what it declines and what it does not model, check by check.
- [Generated code, in full](GENERATED_CODE_RULES.md): the rules behind every form of helper, argument and call Towel writes.
- [Type checking, in full](TYPE_CHECKING_RULES.md): how Towel checks and annotates what it writes in a typed project.
- [Cross-module sharing, in full](CROSS_MODULE_RULES.md): which imports Towel writes between modules, and every reason it declines one.
- [Python API, in full](PYTHON_API_REFERENCE.md): every engine option, the proposal objects and the API's troubleshooting notes.
- [Design proposals and experiments](proposals/README.md): implementation status, deferred designs and dated measurements.
