# Performance roadmap: what is worth doing, in what order

Status: measured 2026-09-17 on Towel's own source (16k lines, 28 files) and
on arrow (10k lines); item 1 applied the same day, items 2 to 5 in the days
after (see *Done since*, at the end). The Rust question is answered by the
recorded decision rule, not by this document. Function names below are as
they were when measured; `_find_block_pairs_multi_file` is now
`find_block_pairs`, `has_orphaned_variables` is `orphaned_variables`,
`get_bound_variables_in_context` is `bound_variables_in_context`, and
`is_value_producing` is the engine's memoized `_is_value_producing`.

## Where the time goes

`towel preview src/towel`, serial (`TOWEL_WORKERS=1`), 70,576 candidate
pairs, 28 proposals, 14.6 s wall on an idle machine (profiled: 52.5 s, so
shares are the reliable figure):

| Phase | Share | What it is |
|---|---|---|
| parse + scope analysis | 1% | |
| pairing (`_find_block_pairs_multi_file`) | 12% | block enumeration and signatures 9%, the pairing loop with 4.67M `quick_filter` calls 3% |
| semantic guards via `_block_rejected` | 36% | five whole-function walks per (function, block), memoized but with 34k misses; **75% of that time was reached through same-file clustering**, not the pair loop |
| clustering's own unification and call generation | <1% | |
| unification (`_unify_memoized`) | 15% | two thirds of it is `get_bound_variables_in_context`, which `ast.unparse`s every visited node (706k calls) |
| post-unification checks | 21% | `_thunk_uncertain_free_variables` 11% (re-walks the function per call), `has_orphaned_variables` 9% |
| other per-pair work | 13% | `is_value_producing` unmemoized in the pair loop, binding snapshots, structural similarity, nested visitor classes rebuilt per call |

Roughly 65% of self time is generic AST traversal (`iter_child_nodes`,
`isinstance`, `iter_fields`, `ast.walk`). Most of it is repeated work: the
same function is re-walked once per block it contains.

Rejection funnel: 64,120 of 70,576 pairs are rejected before unification
(nested bindings 23k, frame-sensitive 14k, not structurally similar 12k);
~6,500 reach the unifier; ~980 unify; 823 fail a later check; 45 survive to
clustering; 28 remain after overlap filtering.

arrow has the same shape: guards 48%, clustering 27%, unification 4%.

## Exact fixes, in order of payoff per line changed

1. **Clustering filters before guards** (done, `_add_clustered_replacements`):
   the size gate and signature filter are constant-time and reject most
   candidate blocks; the five guards ran first. Reordering removed 35% of
   guard executions on src/towel (35,481 to 22,993) with an identical proposal
   list. Wall time under load went from 21.9 s to 19.0 s.
2. **Per-function facts computed once.** `nested_scopes_cross_block_boundary`
   walks the whole function for every block; `locally_bound_names` and
   `definitely_bound_before/after` re-walk it inside
   `_thunk_uncertain_free_variables` and `has_orphaned_variables`. Computing
   the loaded-name sets, the local binding set, and the definite-assignment
   state at each statement once per function, then querying per block, makes
   these O(block) instead of O(function) per block. This is the same fix
   pattern as the rebinding guard (97 s to 6 s in September). Expected: most
   of the guard and dataflow share, about 40 to 50%.
3. **`get_bound_variables_in_context` without `ast.unparse`.** Identity or a
   precomputed path replaces string matching: about 10%.
4. **Memoize `is_value_producing` in the pair loop; hoist visitor classes
   defined inside functions** (234k `__build_class__` calls): a few percent.
5. **Bucket key refinement.** Adding first and last statement type to the
   signature bucket key is exact under `quick_filter`'s contract and cuts the
   4.67M filter calls. Small here, larger on 100k-line projects where pairing
   is a third of the time (Sphinx, per the 2026-09-14 profile).

Items 2 to 4 are pure-Python and change no output, so the goldens and the
hostile batteries verify them exactly.

## Items 3 to 5 (done 2026-09-18)

Cached `ast.unparse` in the unifier's bound-variable search, `is_value_
producing` memoized per block, five per-call visitor classes hoisted, and
the bucket key extended to the whole statement-type sequence (exact: the
unifier cannot unify statements of different kinds). Function calls on
Towel's source: 339.8M before this round, 246.0M after (−28%; −47% from
the 464.5M measured before item 1), proposal list identical.

## End-to-end measurement after items 1 and 2 (2026-09-18)

Serial `towel dry`, out of place, idle machine, two runs each agreeing
within 0.3 s:

| Target | 1.618 release | main, `--no-types --no-format` | main, defaults |
|---|---|---|---|
| h2 (5k lines) | 5.2 s, 14 applied | 7.0 s, 20 applied | 11.9 s, 20 applied |
| Towel (16k lines) | 47.8 s, 41 applied | 33.9 s, 45 applied | 41.8 s, 45 applied |

On Towel's source the engine is 29% faster while applying 10% more
refactorings (the assignment-form extractions the orphan fix unblocked):
1.17 s per applied refactoring became 0.75 s. h2 is too small for the
search-phase savings to show; its per-refactoring time is unchanged (0.37 s
against 0.35 s) and it simply has more to apply. Type inference,
verification and formatting cost about 0.2 to 0.25 s per applied
refactoring on top (one mypy reveal, the subtype probes, and a before-and-
after check of each modified file, incremental after the first build).

## Beyond pairwise matching

The unifier already takes N blocks; the pipeline around it (snapshots, return
variables, extraction, instantiation, placement) is written for two, with
same-file clustering adding further sites after a pair unifies. Grouping
blocks first by a skeleton (names, constants, and attribute names abstracted)
would attack the quadratic pairing share, but a skeleton class is a
*sufficient* condition for unifiability, not a necessary one: on src/towel
1,581 pairs share a skeleton class while about 980 pairs actually unify, and
the sets overlap without containing each other. A skeleton-first scheme
therefore changes which proposals are found unless it keeps the pairwise
search as a fallback, and the recorded note is that the goldens would move.
Its payoff is the pairing and per-pair overhead share, 12 to 17% here and
about a third on Sphinx, not the analysis cost that dominates today.
Recommendation: after items 2 to 4, re-measure; take this on only if pairing
still dominates on the large projects.

## Rust

The recorded decision rule (handoff of 2026-09-14) is to port only if
large-project timings still exceed minutes after the Python work, or if
editor-latency becomes a goal. A compiled tree walker removes the traversal
share, but most of that traversal is the repeated work items 2 to 4
eliminate exactly, so the Rust figure must be re-measured after them.
The baseline for the rule when this was written: 16k lines in 6 s forked
(15 s serial); at `5ff2458` (September 19, 2026, Apple M5 Max,
`TOWEL_WORKERS=1`, Python 3.12) the serial `towel dry` figure on that
day's 22,690-line source, 15 applied, is 8.4 s without the type checker
and formatter and 11.9 s with them (docs/KNOWN_LIMITATIONS.md); corpus
worst cases networkx about 6 min and Sphinx about 340 s of serial initial
analysis.

## Done since (2026-09-18)

Beyond the items above, the engine now computes what it can once per
statement rather than once per block (`statement_facts.py`,
`structural_memo.py`, the weak per-node memos), keys every id-keyed cache
through one `BoundedCache`, re-pairs only changed files in later global
passes, and clusters returning helpers as well as non-returning ones. The
clustering scan of a file runs once per distinct template rather than once
per pair. Pair evaluation keeps one proposal per identity (helper body,
home and sites) and declines the rest as `duplicate_proposal`: sixty
near-identical 38-line functions under `--max-pairs 200000` peaked at
33.6 GB before and 0.74 GB after (`1db56a1`). The candidate-pair budget
(`--max-pairs`, 20,000,000 by default) leaves out the largest groups of
similar blocks with a warning when the projected pair count exceeds it,
and the verdict of the instantiation check is memoized on the helper, the
call and the block's structure. The CHANGELOG's `[Unreleased]` section
records each step with its measurement; the current end-to-end figures are
in docs/KNOWN_LIMITATIONS.md.
