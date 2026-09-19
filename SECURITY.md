# Security policy

Towel rewrites source code. Review the generated diff and run the affected project's own tests before adopting it; see [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for what is verified and what is not.

## Supported versions

Security fixes are provided for the latest released version, currently **1.732.post1**. Earlier versions are not supported; the PyPI releases `1.0.0`–`1.0.4` are yanked for broken import handling and are not a recommended installation target. Upgrade to the latest release rather than relying on a fix to an older one.

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's **"Report a vulnerability"** button on this repository's **Security** tab (Security → Advisories → Report a vulnerability). That opens a private security advisory visible only to the maintainer. Please do not open a public issue for a security report, and do not include credentials or confidential source code in a report.

This is a solo-maintained project with no formal response-time commitment. Reports are handled on a best-effort basis; you will receive an acknowledgment when a report is triaged. If a report leads to a fix, it ships in a new release, and the affected versions are noted in the advisory and the [changelog](CHANGELOG.md).

## Execution and source safety

Towel's own analysis parses Python syntax without executing the analyzed project. Optional type checkers read project source and configuration: mypy runs in an owned worker with the project's checking rules, while project plugins, configured executables and report destinations are disabled; pyright runs as a subprocess with Towel's interpreter (`--pythonpath`), so a project `venv` setting does not select an executable. Python module launches for mypy, pyright and Ruff use isolated mode (`-I`), and child tool environments exclude inherited Python startup/import settings, including for PATH executable fallbacks. Whole-project pyright verification uses private source/stub/configuration snapshots. Checker failure is distinct from a successful check and cannot certify a generated change. The `pyright` PyPI package downloads a Node runtime on first use. Pass `--no-types` when analyzing code you do not trust. The behavioral test harness executes fixtures and must be used only on trusted code. The rename assistant prints a prompt containing source excerpts for the user to transfer manually; consider the destination before sharing confidential code.

Directory refactoring excludes symlinked Python files and refuses overlapping input/output trees. Temporary type-checker copies (`_towel_probe_*.py`) are removed on exit, including on SIGTERM, and are never analyzed as source. Generated files are compiled before writing. Writes use staged byte plans, per-file atomic replacement, rollback, and an interruption-recovery journal. Apply and recovery require exclusive write access; an unrelated writer can race snapshot checks. A batch is not globally atomic to readers, and a sequence of extractions is not a single atomic operation. Compilation does not prove behavior preservation. Static analysis cannot fully model reflection, dynamic imports, arbitrary callbacks, or external effects. Keep the original version under source control and review every generated diff.
