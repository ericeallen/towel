# Preparing a release

Towel remains experimental. The proposed next release is `1.1.0a1`, an alpha prerelease. Preparing artifacts does not authorize uploading them, changing repository visibility, creating remote tags, or contacting users. Publication is a separate maintainer decision.

## Recorded release history

Checked on September 12, 2026:

- [PyPI's project metadata](https://pypi.org/pypi/code-towel/json) lists versions `1.0.0` through `1.0.4`; all of their wheel and source artifacts are yanked with the reason `broken import handling`. Those versions were uploaded December 3–4, 2025. A yanked version is still used; do not rebuild and attempt to replace its files.
- The GitHub repository is private. Authenticated inspection found historical releases `v0.5.0` and `v0.5.1`, an active CI workflow, and an unprotected `main` branch. These are observations, not guarantees about future settings.
- The public PyPI description still represents the earlier release. A new distribution must carry the current experimental-status documentation and Python requirement.

Recheck live version availability immediately before publishing. The proposed alpha number is not reserved.

## Local evidence required

Use Python 3.13 for the pinned quality tools and `uv sync --frozen --extra dev`. Run the commands documented in the README: formatting, lint, typing, Bandit, the full tests, the unconditional 85% coverage gate, and a wheel/source build. Repeat the full tests and coverage on every supported interpreter, currently Python 3.11–3.13.

For the exact candidate commit:

1. Record the commit, tool/interpreter versions, commands, exit statuses, and coverage results. Review every changed golden snapshot; regeneration is not validation.
2. Run representative consumer projects' own tests before and after real transformations in disposable copies. Record upstream revisions, changed files/proposals, baseline failures or skips, and after-test results. A no-change run or compilation alone is not a consumer equivalence check.
3. Inspect both distributions, check metadata and license inclusion, install the wheel into a clean environment, and exercise its CLI entry points. Ensure the version matches the proposed prerelease throughout the metadata.
4. Audit the resolved runtime and development dependencies and perform a secret scan of the release contents and tracked history. Record the scope and limitations of each scan; a clean result does not guarantee absence of vulnerabilities or secrets.
5. Review the current audit report and unresolved limitations. Confirm generated documentation does not claim unconditional behavioral equivalence or production stability.

`just release VERSION` prepares local artifacts. Inspect its current recipe before use; it does not replace the full evidence above or authorize publication.

## Maintainer decisions before publication

- Choose the exact unused prerelease version and approve the reviewed artifact hashes.
- Decide whether and when to make the repository public. Configure CI as a required check before relying on branch protection; verify the actual remote settings.
- Establish which releases will receive security fixes and a working confidential reporting channel. [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository) is available for public repositories. Verify its report button after enabling it, or explicitly document another approved private channel. Do not invent a contact address or response-time commitment.
- Approve the publication destination and release notes. Preserve the historical yanks unless a separate reviewed decision changes their status.

No security support window or response-time promise is established by this document. Update [SECURITY.md](../SECURITY.md) when those decisions are made.
