# Retained 1.772 validation evidence

These are historical observations, not promises about a new release or resource
minimums. The regression tests use them offline; they do not rerun the corpus,
require the original workstation, or time the machine running pytest.

The three text files are unmodified copies from
`~/Knowledge/handoffs/towel/20260924T195010Z-claude-dbda5c50-1772-pre-round3-evidence/release-1772/`:

| Local file | Original file |
| --- | --- |
| corpus-full.txt | corpus-2b399ef-harness.log |
| corpus-sphinx.txt | corpus-29454ab-sphinx-harness.log |
| release-record.txt | tag-v1772.txt |

Their SHA-256 digests are retained in `context.toml`. The whitespace hook
exempts these raw text records so it cannot alter the archived bytes.

The full corpus log contains 141 project outcomes, including a Sphinx timeout.
The later Sphinx-only log replaces that project's outcome; it is not a 142nd
project or evidence that the original run passed. The retained release record
is the annotation prepared for publication, not an independent query of remote
tag or CI state. Its passed-test counts corroborate the readiness table.

`context.toml` preserves the operator-recorded environment and suite details
from `docs/PRODUCTION_READINESS.md` at `088a4c7` and the September 26 open-issues
handoff. The harness logs themselves do not record the host's hardware, VM
allocation, local operating system, or all suite skip counts. Those fields
are explicitly human-recorded provenance, not facts inferred from today's
machine. Sphinx ran alone on the rerun; the preceding corpus used four jobs.

Keep this evidence with its release. For a later release, retain new completed
outputs and the environment that produced them, then add its documentation
checks. Never relabel an old measurement as new or replace it with an expected
number merely to make a test pass. Corrections need corroborating evidence and
an explanation; see the September 27 release-documentation decision in
`docs/DECISIONS.md` and the policy-regression rule in `CONTRIBUTING.md`.
