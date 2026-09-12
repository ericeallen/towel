# Security policy

Towel is experimental. Review generated code and run the affected project's own tests before using it. No supported-release matrix or response-time commitment has been established for the next public release. The historical PyPI releases `1.0.0`–`1.0.4` are yanked for broken import handling; they are not a recommended installation target.

## Reporting

A confidential reporting channel has not yet been verified. Do not put credentials or confidential source code in public issues. Before publication, the maintainer must establish a private channel and specify which releases receive security fixes. GitHub private vulnerability reporting requires a public repository; the repository was private when checked on September 12, 2026. Enabling and testing that channel is a publication prerequisite if it is selected. See [release preparation](docs/RELEASING.md) for the remaining maintainer decisions.

## Execution and source safety

Towel's analysis parses Python syntax without executing the analyzed project. The behavioral test harness executes fixtures and must be used only on trusted code. The rename assistant prints a prompt containing source excerpts for the user to transfer manually; consider the destination before sharing confidential code.

Directory refactoring excludes symlinked Python files and refuses overlapping input/output trees. Generated files are compiled before writing. Writes use staged byte plans, per-file atomic replacement, rollback, and an interruption-recovery journal. Apply and recovery require exclusive write access; an unrelated writer can race snapshot checks. A batch is not globally atomic to readers, and a sequence of extractions is not a single atomic operation. Compilation does not prove behavior preservation. Static analysis cannot fully model reflection, dynamic imports, arbitrary callbacks, or external effects. Keep the original version under source control and review every generated diff.
