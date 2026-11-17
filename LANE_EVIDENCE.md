# Post-integration-3 evidence

All commands ran in this worktree with `TOWEL_WORKERS=2`. Python versions: 3.13.7 and 3.15.2. Logs are in `/tmp/towel-post-int3/`. No full-suite run was made.

## Required module matrix

Commands used `.venv/bin/python -m pytest -q <modules>` for 3.13 default, `.venv/bin/python -m pytest -v <modules> --towel-strict` for 3.13 strict, and `.venv-315/bin/python -m pytest -q <modules>` for 3.15 default.

The first matrix ran before two corrections to test expectations from the required integration merge. These are not claimed as clean runs:

| Configuration | Initial result | Log |
| --- | --- | --- |
| 3.13 default | 4 failed, 1844 passed, 22 skipped, 2 warnings | modules-313-default.log |
| 3.13 strict | 4 failed, 1844 passed, 22 skipped, 2 warnings | modules-313-strict.log |
| 3.15 default | 10 failed, 1848 passed, 12 skipped, 808 warnings | modules-315-default.log |

All four suppressing-context parameters in `test_relaxed_companions_late.py::test_relaxed_suppressing_context` failed in each configuration. The byvalue merge deliberately refuses possibly unbound reads; the old tests expected extraction or a profitability refusal. The correction requires `thunk_of_possibly_unbound_local`, zero extraction, identical AST, and equivalent output for both Boolean branches, including the exact `UnboundLocalError` message.

The six additional 3.15 failures were `test_hostile_battery.py::test_refactoring_preserves_program_output` parameters `h39_augassign_after_block`, `h51_semicolons`, `h52_del_after_block`, `h53_nested_func_reads_retvar`, `r79_list_display_in_loop`, and `r131_weakref_and_id_identity`. The first five now choose whole-body candidates whose strict lifetime proof is unavailable on newer runtimes. The last safely extracts id3/id4 while retaining id1/id2's lifetime refusal. Behavior, scope, module-interface and refusal checks remain. All ten failures also reproduced with product code from `ux/integration`.

Whole-module reruns, after correction:

| Command suffix | Result | Log |
| --- | --- | --- |
| 3.13: `-q tests/test_relaxed_companions_late.py` | 160 passed | companions-313-default-final.log |
| 3.13: same, `--towel-strict` | 160 passed | companions-313-strict-final.log |
| 3.15: `-q tests/test_hostile_battery.py tests/test_relaxed_companions_late.py` | 793 passed, 130 warnings | modules-315-rerun-final.log |

The final exclusion-scope correction was checked by rerunning all seven source/staging modules in all three configurations:

```
tests/test_in_place_stage.py tests/test_out_of_place_stage.py
tests/test_filesystem_guards.py tests/test_runtime_staging.py
tests/test_runtime_source_inputs.py tests/test_runtime_typed_skips.py
tests/test_unparsed_program.py
```

| Configuration | Result | Log |
| --- | --- | --- |
| 3.13 default | 150 passed, 1 skipped, 2 warnings | source-313-default-final.log |
| 3.13 strict | 150 passed, 1 skipped, 2 warnings | source-313-strict-final.log |
| 3.15 default | 141 passed, 10 skipped, 2 warnings | source-315-default-final.log |

No new skips were added. No unresolved test failures remain in this validation scope.

## Regression evidence and diagnoses

1. `crash-before.log` reproduces the real Python 3.15 dict-unpacking crash. `test_resource_guard::test_unpacking_dict_comprehension_keeps_its_resource_checks` additionally proves that the remaining expression is still guarded when `DictComp.value` is absent. It failed before the fix. The runtime schema has optional **value**, not key as the brief said. Comparing AST schemas across 3.13/3.14/3.15 found the new `Interpolation.format_spec` optional child; its absent/present tests live in `test_new_python_syntax::test_template_optional_format_spec_keeps_resource_checks`. TemplateStr uses a child list. The new lazy-import field is an optional scalar, not a child. Existing optional annotation/default children already have guards or use `ast.iter_child_nodes`. The abandoned synthetic optional-key probe in `guard-before.log` is not evidence about Python's actual schema.
2. `test_reason_sentences` completeness and plain-sentence tests failed before adding the resource explanation (`reasons-before.log`), then all 92 passed.
3. Existing undecodable-file and syntax-error diagnostics tests failed before their corrections (`diagnostics-before.log`). Final matrix covers both.
4. `explicit-mode-before.log`, `fixtures-before.log`, and `lifetime-before.log` record the mode, fixture-signature and strict-newer-runtime expectation failures. Corrections require explicit modes and the exact refusal plus unchanged source; they add no skips.
5. `r1792_external_exception_partial` still extracts once on both interpreters. On 3.13 the original finalizes outer then inner and the extraction reverses them; on 3.15 both finalize inner then outer. This falls under relaxed finalization assumptions, and the assertion now requires equality on newer runtimes while retaining normalized event/effect checks. `r9sb_lambda_capture_across_modules` declines with `unification_failed` in both modes and both host variants; it is a signature refusal, not a lifetime refusal. Neither case showed a new behavioral defect.
6. Null bytes fail in `decode_source` on 3.14+, before the parser check. Strict's historical decode-skip policy is retained. Tests assert that stage and the explicit decode complaint, while relaxed still warns once.
7. `typed-before.log` demonstrates the false type-reachability warning for an already skipped file. `typed-after.log` and final source checks retain actual type-error coverage while removing that warning.
8. `excluded-before.log` reproduces warnings/counts for intentionally excluded invalid files. Explicit-target controls retain precedence. Review then caught matching exclusions against ancestors outside the project; `exclusion-root-before.log` fails with exit 1, and `exclusion-root-after.log` reports 29 passed after the scan-root correction. Independent CLI smoke checks also covered an unmarked project: preview/dry both extract once, do not warn about excluded fixtures, and dry preserves excluded bytes.

## Acceptance and quality

Commands:

```
PATH="$PWD/.venv/bin:$PATH" just check
.venv/bin/python /Users/ericeallen/Code/towel-user-audit-20261114/remediation/accept.py .venv
.venv/bin/python /Users/ericeallen/Code/towel-user-audit-20261114/remediation/accept.py .venv -- --strict
```

`check-final.log`: `Quality checks passed.` This includes formatting, lint, mypy and security checks. All local commits ran hooks. `git diff --check` passed; a credential-pattern scan found no credential-shaped additions.

Final acceptance logs (`accept-relaxed-final.log`, `accept-strict-final.log`) each end `failures: none`: all 50 driver outputs identical, all five controls untouched. Relaxed changes 30/45 duplication cases; strict changes 16/45. Outputs containing `_towel_`: 1 relaxed, 7 strict; outputs adding a lambda: 1 in each. These acceptance counters are retained, not represented as zero.

The required integration merge was made at `b61e9ba2`, before validation. The coordinator later advanced integration to `60135e34` (reach lane); that subsequent merge is outside this tested baseline. No local golden regeneration, version change or guide edit was made. Golden/doc changes in ancestry came from the required integration merge.

Environments occupy 189 MiB and 178 MiB; lane logs occupy less than 1 MiB.

## The 30 matrix modules

```
tests/test_analysis_sessions.py
tests/test_analysis_source_diagnostics.py
tests/test_annotation_guard_runtime.py
tests/test_annotation_imports.py
tests/test_candidate_index.py
tests/test_cli_input_errors.py
tests/test_cross_module_opt_in.py
tests/test_function_index.py
tests/test_generated_helper_aliases.py
tests/test_generated_helper_reuse.py
tests/test_hostile_battery.py
tests/test_hostile_crossfile_battery.py
tests/test_new_python_syntax.py
tests/test_no_extract.py
tests/test_pair_budget.py
tests/test_pair_partner_eligibility.py
tests/test_pipeline_phases.py
tests/test_reason_sentences.py
tests/test_refactor_engine_edge_cases.py
tests/test_relaxed_companions_late.py
tests/test_relaxed_review.py
tests/test_resource_guard.py
tests/test_resource_reexports.py
tests/test_runtime_source_inputs.py
tests/test_runtime_typed_skips.py
tests/test_type_checking_imports.py
tests/test_typing_remedies.py
tests/test_unify_audit_scenarios.py
tests/test_unify_escaping_lambdas.py
tests/test_unparsed_program.py
```

## Local commits

```
19177913 Keep unpacking comprehensions analyzable when their value is absent
f8949864 Explain resource ownership refusals in the results table
271edf67 Explain why the named single-file input cannot be decoded
709f2a45 Give skipped syntax errors the same line-first explanation as refusals
bd1a8761 Select relaxed mode when asserting invalid-input skips
0d732c04 Call the interpreter-specific typing fixtures with their actual signatures
36203ac4 Pin the strict f-string refusal where frame cleanup is uncertified
48e50329 Require strict ownership refusals for audit scenarios on newer runtimes
593f3de2 Check the closed callback refusal on runtimes without ownership proofs
251e8f41 Expect newer frame cleanup to preserve the exception fixture finalizer order
94e33e61 Keep the lambda signature refusal independent of runtime lifetime certification
dd988f56 Assert null-byte failures at the decoding stage used by each interpreter
bf098f23 Merge branch 'ux/integration' into ux/post-int3-fixes
2d22e883 Leave skipped source out of type reachability diagnostics
936962d7 Respect excluded inputs before warning about parse failures
1185b787 Keep the source policy dependency with the other project imports
bf1ff72b Reconcile callback extractions with strict newer-runtime refusals
17d79b99 Keep possibly unbound reads behind their original condition
34d1aba7 Match excluded names only within the scanned project
```
