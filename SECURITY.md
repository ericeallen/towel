# Security policy

Towel is experimental. Review generated code and run the affected project's tests before using it. No supported-release matrix or response-time commitment has been established for the next public release.

Before publication, the maintainer must enable and verify a private reporting channel, such as GitHub private vulnerability reporting, and state which releases receive security fixes. Do not post credentials or confidential source code in public issues.

Towel's analysis parses Python syntax without executing the analyzed project. The behavioral test harness executes fixtures and must be used only on trusted code. The rename assistant prints a prompt containing source excerpts for the user to transfer manually; consider the destination before sharing confidential code.

Directory refactoring excludes symlinked Python files and refuses overlapping input/output trees. Generated files are compiled before writing. Multi-file changes are not an atomic transaction, and static analysis cannot prove behavior preservation for reflection, dynamic imports, arbitrary callbacks, or external effects. Keep the original version under source control and review every generated diff.
