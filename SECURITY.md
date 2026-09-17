# Security policy

Towel rewrites source code. Review the generated diff and run the affected project's own tests before adopting it; see [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for what is verified and what is not.

## Supported versions

Security fixes are provided for the latest released version, currently **1.618**. Earlier versions are not supported; the PyPI releases `1.0.0`–`1.0.4` are yanked for broken import handling and are not a recommended installation target. Upgrade to the latest release rather than relying on a fix to an older one.

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's **"Report a vulnerability"** button on this repository's **Security** tab (Security → Advisories → Report a vulnerability). That opens a private security advisory visible only to the maintainer. Please do not open a public issue for a security report, and do not include credentials or confidential source code in a report.

This is a solo-maintained project with no formal response-time commitment. Reports are handled on a best-effort basis; you will receive an acknowledgment when a report is triaged. If a report leads to a fix, it ships in a new release, and the affected versions are noted in the advisory and the [changelog](CHANGELOG.md).

## Execution and source safety

Towel's analysis parses Python syntax without executing the analyzed project. The behavioral test harness executes fixtures and must be used only on trusted code. The rename assistant prints a prompt containing source excerpts for the user to transfer manually; consider the destination before sharing confidential code.

Directory refactoring excludes symlinked Python files and refuses overlapping input/output trees. Generated files are compiled before writing. Writes use staged byte plans, per-file atomic replacement, rollback, and an interruption-recovery journal. Apply and recovery require exclusive write access; an unrelated writer can race snapshot checks. A batch is not globally atomic to readers, and a sequence of extractions is not a single atomic operation. Compilation does not prove behavior preservation. Static analysis cannot fully model reflection, dynamic imports, arbitrary callbacks, or external effects. Keep the original version under source control and review every generated diff.
